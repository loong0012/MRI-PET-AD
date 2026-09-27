"""
Fold 2 数据诊断脚本（方案 J）

目的：fold 2 在所有 5 个种子上 AUC 均 <0.85（其余 fold 0.90+），
     诊断该 fold 的 test 集是否存在数据问题（疑似标注错误/非典型病例/站点偏移/图像异常）。

分析内容：
  1. 5 fold × 15 模型（5 seeds × 3 snapshots）逐样本预测
  2. fold 级对比：错误数 / 高置信度共识错误数 / 模型间分歧度
  3. fold 2 错误样本明细（Subject, age, 概率均值/标准差, 15 模型投票）
  4. Subject ID 站点编码分析（ADNI ID 中 ADNI 后 3 位为 site code）
  5. fold 2 错误 vs 正确样本的图像质量统计（MRI/PET 强度、有效体素比例）

输出：
  - 控制台打印诊断报告
  - checkpoints/diag_fold2_wrong_samples.csv（fold 2 全部错误样本清单）
"""
import os
os.environ['KMP_DUPLICATE_LIB_OK'] = 'TRUE'

import re
import csv
import numpy as np
import pandas as pd
import torch
import torch.nn.functional as F
from torch.utils.data import DataLoader

try:
    import monai.transforms.transform as _m_tfm
    _m_tfm.MAX_SEED = 2 ** 31
    import sys as _sys
    for _n, _m in list(_sys.modules.items()):
        if _n.startswith("monai") and hasattr(_m, "MAX_SEED"):
            try:
                setattr(_m, "MAX_SEED", 2 ** 31)
            except Exception:
                pass
except Exception:
    pass

from datasets.ADNI import ADNI, ADNI_transform
from models.mymodel import model_ad
from utils.utils import stratified_kfold_indices
from monai.data import Dataset

EXP_DIRS = [
    './checkpoints/ADCN_IN3D_MERGE_V6',
    './checkpoints/ADCN_IN3D_MERGE_S2',
    './checkpoints/ADCN_IN3D_MERGE_S3',
    './checkpoints/ADCN_IN3D_MERGE_S4',
    './checkpoints/ADCN_IN3D_MERGE_S5',
]
SNAPSHOT_EPOCHS = [40, 45, 50]
DATAROOT = './datasets/MRI PET图像'
TARGET_FOLD = 2


def build_model(device):
    return model_ad(dim=128, depth=3, heads=4, dim_head=32, mlp_dim=512,
                    dropout=0.15, num_classes=2).to(device)


def load_state_dict_flexible(model, ckpt_path, device):
    ckpt = torch.load(ckpt_path, map_location=device)
    sd = ckpt['net_model'] if isinstance(ckpt, dict) and 'net_model' in ckpt else ckpt
    model.load_state_dict(sd)
    return model


@torch.no_grad()
def predict_probs(model, loader, device):
    model.eval()
    probs = []
    for batch in loader:
        mri = batch['MRI'].to(device, non_blocking=True)
        pet = batch['PET'].to(device, non_blocking=True)
        logits, _, _ = model(mri, pet)
        p_ad = F.softmax(logits, dim=1)[:, -1]
        probs.append(p_ad.cpu().numpy())
    return np.concatenate(probs)


def site_code(subject):
    """从 sub-ADNI002S4171 提取站点码 002。"""
    m = re.match(r'sub-ADNI(\d{3})S', str(subject))
    return m.group(1) if m else '???'


def image_quality_stats(data_item, test_transform):
    """对单个样本计算 MRI/PET 图像质量统计（加载 + 预处理后）。"""
    ds = Dataset(data=[data_item], transform=test_transform)
    batch = ds[0]
    stats = {}
    for mod in ('MRI', 'PET'):
        img = batch[mod].numpy()[0]  # (1, D, H, W) -> (D, H, W)
        nonzero = img[img != 0]
        stats[f'{mod}_mean'] = float(nonzero.mean()) if len(nonzero) else 0.0
        stats[f'{mod}_std'] = float(nonzero.std()) if len(nonzero) else 0.0
        stats[f'{mod}_nzfrac'] = float((img != 0).mean())
    return stats


def main():
    device = torch.device('cuda:0' if torch.cuda.is_available() else 'cpu')
    print(f'[Diag] device={device}')

    adni = ADNI(dataroot=DATAROOT, label_filename='ADNI.csv', task='ADCN').data_dict
    all_labels = [int(d['label']) for d in adni]
    kfold_splits = list(stratified_kfold_indices(all_labels, n_splits=5, seed=42))
    _, test_transform = ADNI_transform(aug='False')

    model = build_model(device)

    # {fold: {'probs': (n_models, N), 'y': (N,), 'subjects': [..], 'ages': [..]}}
    fold_data = {}

    for fold_idx, (train_idx, test_idx) in enumerate(kfold_splits):
        test_data = [adni[int(i)] for i in test_idx]
        test_dataset = Dataset(data=test_data, transform=test_transform)
        test_loader = DataLoader(test_dataset, batch_size=4, shuffle=False, num_workers=0)
        y = np.array([int(d['label']) for d in test_data])
        subjects = [d['Subject'] for d in test_data]
        ages = [d.get('age', 75.0) for d in test_data]

        all_model_probs = []
        model_tags = []
        for d in EXP_DIRS:
            for e in SNAPSHOT_EPOCHS:
                sp = os.path.join(d, str(fold_idx), f'snapshot_epoch{e}.pt')
                if not os.path.isfile(sp):
                    continue
                load_state_dict_flexible(model, sp, device)
                p = predict_probs(model, test_loader, device)
                all_model_probs.append(p)
                model_tags.append(f'{os.path.basename(d)}_e{e}')
        P = np.stack(all_model_probs, axis=0)  # (n_models, N)
        fold_data[fold_idx] = dict(P=P, y=y, subjects=subjects, ages=ages,
                                   n_models=P.shape[0])
        print(f'[Diag] fold {fold_idx}: N={len(y)} (AD={y.sum()}, CN={(y==0).sum()}), '
              f'models={P.shape[0]}')

    # ============ 1. fold 级错误模式对比 ============
    print('\n' + '=' * 78)
    print('1. 5-fold 错误模式对比（15 模型集成，阈值 0.5）')
    print('=' * 78)
    print(f'{"fold":>5} {"N":>4} {"AD":>4} {"errors":>7} {"consensus":>9} '
          f'{"hiConf":>7} {"meanProbErr":>12} {"meanStd":>8} {"ensAcc":>7}')
    fold_summary = {}
    for fi in range(5):
        d = fold_data[fi]
        P, y = d['P'], d['y']
        p_ens = P.mean(axis=0)                       # (N,)
        votes_ad = (P >= 0.5).sum(axis=0)            # 15 个模型中投 AD 的数量
        pred = (p_ens >= 0.5).astype(int)
        wrong = (pred != y)
        n_err = int(wrong.sum())
        # 共识错误：≥12/15 模型一致投票到错误类别
        consensus_wrong = ((y == 1) & (votes_ad <= 3)) | ((y == 0) & (votes_ad >= 12))
        n_cons = int(consensus_wrong.sum())
        # 高置信度错误：集成概率错误侧且置信度 >0.8
        hi_conf = wrong & (np.abs(p_ens - 0.5) > 0.3)
        n_hi = int(hi_conf.sum())
        # 错误样本上的平均集成概率（错误侧置信度）
        mean_prob_err = float(p_ens[wrong].mean()) if n_err else 0.0
        # 模型间分歧度（所有样本上概率 std 均值）
        mean_std = float(P.std(axis=0).mean())
        acc = 1 - n_err / len(y)
        fold_summary[fi] = dict(n_err=n_err, consensus=n_cons, hi_conf=n_hi,
                                mean_std=mean_std, acc=acc, wrong_mask=wrong,
                                consensus_mask=consensus_wrong)
        print(f'{fi:>5} {len(y):>4} {int(y.sum()):>4} {n_err:>7} {n_cons:>9} '
              f'{n_hi:>7} {mean_prob_err:>12.3f} {mean_std:>8.4f} {acc:>7.3f}')

    # ============ 2. fold 2 错误样本明细 ============
    print('\n' + '=' * 78)
    print(f'2. Fold {TARGET_FOLD} 错误样本明细（按错误置信度排序）')
    print('=' * 78)
    d = fold_data[TARGET_FOLD]
    P, y = d['P'], d['y']
    p_ens = P.mean(axis=0)
    p_std = P.std(axis=0)
    votes_ad = (P >= 0.5).sum(axis=0)
    pred = (p_ens >= 0.5).astype(int)
    wrong = (pred != y)
    wrong_rows = []
    for i in np.where(wrong)[0]:
        subj = d['subjects'][i]
        true_label = 'AD' if y[i] == 1 else 'CN'
        # 错误置信度：如果真值 CN 但预测 AD，置信度=p_ens；真值 AD 预测 CN，置信度=1-p_ens
        wrong_conf = p_ens[i] if y[i] == 0 else (1 - p_ens[i])
        wrong_rows.append(dict(
            subject=subj, site=site_code(subj), age=round(float(d['ages'][i]), 1),
            true=true_label, p_AD=round(float(p_ens[i]), 4),
            p_std=round(float(p_std[i]), 4),
            votes_AD=int(votes_ad[i]), votes_CN=int(P.shape[0] - votes_ad[i]),
            wrong_conf=round(float(wrong_conf), 4),
            n_models=P.shape[0],
        ))
    wrong_rows.sort(key=lambda r: -r['wrong_conf'])
    print(f'{"Subject":<20} {"site":>5} {"age":>6} {"true":>5} {"p(AD)":>7} '
          f'{"std":>6} {"voteAD":>7} {"voteCN":>7} {"errConf":>8}')
    for r in wrong_rows:
        flag = '***' if r['wrong_conf'] > 0.8 else ('** ' if r['wrong_conf'] > 0.7 else '   ')
        print(f'{r["subject"]:<20} {r["site"]:>5} {r["age"]:>6} {r["true"]:>5} '
              f'{r["p_AD"]:>7.4f} {r["p_std"]:>6.4f} {r["votes_AD"]:>7} '
              f'{r["votes_CN"]:>7} {r["wrong_conf"]:>8.4f} {flag}')

    # ============ 3. 站点分析 ============
    print('\n' + '=' * 78)
    print('3. 站点分析（Subject ID 中 ADNI 后 3 位 = site code）')
    print('=' * 78)
    for fi in range(5):
        dd = fold_data[fi]
        pp = dd['P'].mean(axis=0)
        pr = (pp >= 0.5).astype(int)
        w = (pr != dd['y'])
        sites = [site_code(s) for s in dd['subjects']]
        df = pd.DataFrame({'site': sites, 'wrong': w, 'y': dd['y']})
        site_stat = df.groupby('site').agg(n=('wrong', 'size'),
                                           errors=('wrong', 'sum')).reset_index()
        site_stat['err_rate'] = site_stat['errors'] / site_stat['n']
        site_stat = site_stat.sort_values('n', ascending=False)
        print(f'\n--- Fold {fi} 站点分布（错误率）---')
        for _, row in site_stat.iterrows():
            mark = ' <<<' if row['errors'] >= 3 else ''
            print(f"  site {row['site']}: n={int(row['n']):>3}, "
                  f"errors={int(row['errors']):>2}, err_rate={row['err_rate']:.2f}{mark}")

    # fold2 错误样本站点集中度
    sites_f2 = [site_code(s) for s in fold_data[TARGET_FOLD]['subjects']]
    wrong_sites = [site_code(s) for s in
                   [fold_data[TARGET_FOLD]['subjects'][i] for i in np.where(wrong)[0]]]
    print(f'\nFold {TARGET_FOLD} 错误样本站点分布: '
          f'{dict(pd.Series(wrong_sites).value_counts())}')

    # ============ 4. 图像质量统计（fold 2 错误 vs 正确）============
    print('\n' + '=' * 78)
    print(f'4. Fold {TARGET_FOLD} 图像质量统计（错误样本 vs 正确样本）')
    print('=' * 78)
    test_data = [adni[int(i)] for i in kfold_splits[TARGET_FOLD][1]]
    wrong_idx = set(np.where(wrong)[0].tolist())
    quality_rows = []
    for i, item in enumerate(test_data):
        st = image_quality_stats(item, test_transform)
        st['wrong'] = i in wrong_idx
        st['subject'] = item['Subject']
        quality_rows.append(st)
    qdf = pd.DataFrame(quality_rows)
    for grp_name, grp in [('WRONG', qdf[qdf['wrong']]), ('CORRECT', qdf[~qdf['wrong']])]:
        print(f'\n  [{grp_name}] n={len(grp)}')
        for col in ['MRI_mean', 'MRI_std', 'MRI_nzfrac', 'PET_mean', 'PET_std', 'PET_nzfrac']:
            print(f'    {col:<14}: mean={grp[col].mean():.4f}, std={grp[col].std():.4f}, '
                  f'min={grp[col].min():.4f}, max={grp[col].max():.4f}')

    # 异常图像检测：nzfrac 离群
    print('\n  [离群样本] MRI_nzfrac 或 PET_nzfrac 在全体 ±2σ 之外的样本：')
    for col in ['MRI_nzfrac', 'PET_nzfrac', 'MRI_std', 'PET_std']:
        mu, sd = qdf[col].mean(), qdf[col].std()
        out = qdf[(qdf[col] < mu - 2 * sd) | (qdf[col] > mu + 2 * sd)]
        for _, r in out.iterrows():
            print(f'    {r["subject"]:<20} {col}={r[col]:.4f} '
                  f'(mean={mu:.4f}, ±2σ) wrong={r["wrong"]}')

    # ============ 保存 fold 2 错误样本 CSV ============
    out_csv = './checkpoints/diag_fold2_wrong_samples.csv'
    with open(out_csv, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f)
        w.writerow(['subject', 'site', 'age', 'true_label', 'p_AD_ens', 'p_std',
                    'votes_AD', 'votes_CN', 'wrong_confidence', 'n_models'])
        for r in wrong_rows:
            w.writerow([r['subject'], r['site'], r['age'], r['true'], r['p_AD'],
                        r['p_std'], r['votes_AD'], r['votes_CN'], r['wrong_conf'],
                        r['n_models']])
    print(f'\n[Diag] fold {TARGET_FOLD} 错误样本清单已保存 -> {out_csv}')

    # ============ 5. 跨 fold 一致性检查：同一 subject 是否出现在多个 fold 的错误中 ============
    print('\n' + '=' * 78)
    print('5. 跨 fold 错误重叠（同一被试在多个 fold 中被错分——结构性难例）')
    print('=' * 78)
    from collections import Counter
    wrong_subjects = Counter()
    for fi in range(5):
        dd = fold_data[fi]
        pp = dd['P'].mean(axis=0)
        pr = (pp >= 0.5).astype(int)
        for i in np.where(pr != dd['y'])[0]:
            wrong_subjects[dd['subjects'][i]] += 1
    multi = {s: c for s, c in wrong_subjects.items() if c >= 2}
    print(f'在 ≥2 个 fold 中被错分的被试（共 {len(multi)} 个）:')
    for s, c in sorted(multi.items(), key=lambda x: -x[1]):
        # 查该被试的标签
        lab = '?'
        for fi in range(5):
            dd = fold_data[fi]
            if s in dd['subjects']:
                lab = 'AD' if dd['y'][dd['subjects'].index(s)] == 1 else 'CN'
                break
        print(f'  {s:<20} label={lab} 错误次数={c}/5 site={site_code(s)}')


if __name__ == '__main__':
    main()
