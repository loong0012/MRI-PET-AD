"""
快照集成 + 跨种子集成软投票推理脚本（方案2/多种子 配套）

对每个 fold：
  1. 加载各实验目录（不同种子）的快照权重（snapshot_epoch40/45/50.pt，EMA shadow）
  2. 每个快照对 test 集 + train 集 前向得到 softmax(AD) 概率（无 TTA——翻转对 AD 偏侧性特征有害）
  3. 集成模式对比：
     - single               : 各目录 best_ckpt（epoch50 EMA）单独评估
     - snap_ens             : 单目录内 3 快照平均
     - multi_seed           : 所有目录 × 所有快照联合平均（固定 0.5 阈值）
     --- 以下校准模式均在循环结束后用已收集的概率做后处理，无额外前向开销 ---
     - multi_seed_cal       : per-fold Youden's J 阈值（无约束，参考用）
     - multi_seed_cal_range : per-fold Youden's J，阈值限制在 [--thresh_min, --thresh_max]
     - multi_seed_cal_global: 全局 Youden's J（合并所有 fold 训练集概率找单一阈值，同范围约束）

用法（跨种子多目录 + 阈值校准）：
  python ensemble_inference.py --exp_dirs "./checkpoints/ADCN_IN3D_MERGE_V6;./checkpoints/ADCN_IN3D_MERGE_S2;./checkpoints/ADCN_IN3D_MERGE_S3" --calibrate True ...
"""
import os
os.environ['KMP_DUPLICATE_LIB_OK'] = 'TRUE'

import argparse
import csv
import numpy as np
import torch
import torch.nn.functional as F
from torch.utils.data import DataLoader
from sklearn.metrics import roc_auc_score, roc_curve
from sklearn.linear_model import LogisticRegression

# Windows MONAI seed 溢出修复（必须先于 monai transform）
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
from utils.utils import stratified_kfold_indices, stratified_kfold_by_cohort
from monai.data import Dataset


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument('--exp_dirs', type=str, default='',
                   help='分号分隔的多个训练目录（跨种子集成）；为空时回退用 --exp_dir')
    p.add_argument('--exp_dir', type=str, default='', help='单个训练目录（兼容旧用法）')
    p.add_argument('--dataroot', type=str, default='./datasets/MRI PET图像')
    p.add_argument('--task', type=str, default='ADCN')
    p.add_argument('--num_fold', type=int, default=5)
    p.add_argument('--kfold_seed', type=int, default=42, help='ADCN 训练时用的 split seed')
    p.add_argument('--cohort_split', type=str, default='False',
                   help='True 时用队列+标签双重分层 KFold（需与训练时一致）')
    p.add_argument('--model', type=str, default='Transformer')
    p.add_argument('--dim', type=int, default=128)
    p.add_argument('--trans_enc_depth', type=int, default=3)
    p.add_argument('--cross_attn_depth', type=int, default=3)
    p.add_argument('--dropout', type=float, default=0.15)
    p.add_argument('--num_classes', type=int, default=2)
    p.add_argument('--batch_size', type=int, default=4)
    p.add_argument('--num_workers', type=int, default=0, help='Windows 下非 0 易死锁，默认 0')
    p.add_argument('--snapshot_epochs', type=str, default='40,45,50')
    p.add_argument('--calibrate', type=str, default='True',
                   help='True 时用训练集概率做 Youden J 阈值校准')
    p.add_argument('--thresh_min', type=float, default=0.3,
                   help='校准模式下允许的最小阈值')
    p.add_argument('--thresh_max', type=float, default=0.6,
                   help='校准模式下允许的最大阈值')
    p.add_argument('--save_csv', type=str, default='True')
    p.add_argument('--out_name', type=str, default='ensemble_results.csv')
    # 方案 E: TTA 强度扰动
    p.add_argument('--use_tta', type=str, default='False',
                   help='True 时对每个样本做多次强度扰动前向取平均')
    p.add_argument('--tta_factors', type=str, default='1.0,1.05,0.95',
                   help='强度扰动因子列表，逗号分隔')
    # 方案 G: Stacking 元学习器
    p.add_argument('--use_stacking', type=str, default='False',
                   help='True 时用 LogisticRegression 元学习器替代简单概率平均')
    p.add_argument('--stacking_C', type=float, default=0.1,
                   help='LogisticRegression 正则化参数（越小越保守）')
    return p.parse_args()


def build_model(args, device):
    assert args.model == 'Transformer', 'ensemble_inference 目前只支持 model_ad (Transformer)'
    return model_ad(dim=args.dim, depth=args.trans_enc_depth, heads=4,
                    dim_head=args.dim // 4, mlp_dim=args.dim * 4, dropout=args.dropout,
                    num_classes=args.num_classes).to(device)


def load_state_dict_flexible(model, ckpt_path, device):
    ckpt = torch.load(ckpt_path, map_location=device)
    sd = ckpt['net_model'] if isinstance(ckpt, dict) and 'net_model' in ckpt else ckpt
    model.load_state_dict(sd)
    return model


@torch.no_grad()
def predict_probs(model, loader, device, tta_factors=None):
    """返回 (N, ) 的 AD 概率 + (N, ) 标签。
    若 tta_factors 不为 None，对每个样本做多次强度扰动前向取平均（方案 E: TTA）。"""
    model.eval()
    probs, labels = [], []
    factors = tta_factors if tta_factors is not None else [1.0]
    for batch in loader:
        mri = batch['MRI'].to(device, non_blocking=True)
        pet = batch['PET'].to(device, non_blocking=True)
        y = batch['label']
        batch_probs = []
        for f in factors:
            logits, _, _ = model(mri * f, pet * f)
            p_ad = F.softmax(logits, dim=1)[:, -1]
            batch_probs.append(p_ad.cpu().numpy())
        avg_prob = np.mean(batch_probs, axis=0) if len(factors) > 1 else batch_probs[0]
        probs.append(avg_prob)
        labels.append(y.numpy())
    return np.concatenate(probs), np.concatenate(labels)


def cal_metrics(y_true, p_ad, threshold=0.5):
    y_pred = (p_ad >= threshold).astype(np.int64)
    y_true = np.asarray(y_true, dtype=np.int64)
    tp = int(((y_pred == 1) & (y_true == 1)).sum())
    tn = int(((y_pred == 0) & (y_true == 0)).sum())
    fp = int(((y_pred == 1) & (y_true == 0)).sum())
    fn = int(((y_pred == 0) & (y_true == 1)).sum())
    sen = tp / max(1, tp + fn)
    spe = tn / max(1, tn + fp)
    acc = (tp + tn) / max(1, len(y_true))
    f1 = 2 * tp / max(1, 2 * tp + fp + fn)
    auc = roc_auc_score(y_true, p_ad)
    return dict(acc=acc, sen=sen, spe=spe, f1=f1, auc=auc)


def find_youden_threshold(y_true, p_ad, thresh_min=0.0, thresh_max=1.0):
    """在 [thresh_min, thresh_max] 范围内用 Youden's J（TPR - FPR）找最优分类阈值。

    在训练集概率上找阈值，然后应用到 test 集——严格无乐观偏差。
    范围约束用于避免训练集-test 集分布偏移导致的极端阈值（如 0.206）。
    返回 (best_threshold, best_j_score)。
    """
    fpr, tpr, thresholds = roc_curve(y_true, p_ad)
    j_scores = tpr - fpr  # Youden's J = Sens + Spec - 1
    valid = (thresholds >= thresh_min) & (thresholds <= thresh_max)
    if not valid.any():
        # 范围内无候选点（概率整体偏移出范围）：取范围中点
        return (thresh_min + thresh_max) / 2, 0.0
    idx = np.where(valid)[0]
    best_idx = idx[np.argmax(j_scores[idx])]
    return float(thresholds[best_idx]), float(j_scores[best_idx])


def main():
    args = parse_args()
    device = torch.device('cuda:0' if torch.cuda.is_available() else 'cpu')
    do_calibrate = _str2bool(args.calibrate)

    exp_dirs = [d.strip() for d in
                (args.exp_dirs if args.exp_dirs else args.exp_dir).split(';') if d.strip()]
    assert exp_dirs, '必须提供 --exp_dirs 或 --exp_dir'
    print(f'[Ensemble] device={device}')
    print(f'[Ensemble] exp_dirs={exp_dirs}')
    print(f'[Ensemble] calibrate={do_calibrate}  thresh_range=[{args.thresh_min}, {args.thresh_max}]')

    # TTA 配置（方案 E）
    use_tta = _str2bool(args.use_tta)
    tta_factors = [float(f) for f in args.tta_factors.split(',') if f.strip()] if use_tta else None
    if use_tta:
        print(f'[Ensemble] TTA enabled, factors={tta_factors}')

    # Stacking 配置（方案 G）
    use_stacking = _str2bool(args.use_stacking)
    if use_stacking:
        print(f'[Ensemble] Stacking enabled, C={args.stacking_C}')

    # 数据与 split（与训练完全一致：ADCN seed=42）
    adni = ADNI(dataroot=args.dataroot, label_filename='ADNI.csv', task=args.task).data_dict
    all_labels = [int(d['label']) for d in adni]
    if _str2bool(args.cohort_split):
        kfold_splits = list(stratified_kfold_by_cohort(adni, n_splits=args.num_fold, seed=args.kfold_seed))
        print('[Ensemble] Using cohort-stratified KFold splits')
    else:
        kfold_splits = list(stratified_kfold_indices(all_labels, n_splits=args.num_fold, seed=args.kfold_seed))
    _, test_transform = ADNI_transform(aug='False')

    snapshot_epochs = [int(e) for e in args.snapshot_epochs.split(',') if e.strip().isdigit()]

    results = {}        # results[mode] = [(fold_idx, exp_name, metrics), ...]
    fold_records = []   # 校准用的延迟后处理数据（无额外前向开销）
    for fold_idx, (train_idx, test_idx) in enumerate(kfold_splits):
        # ----- test 集 -----
        test_data = [adni[int(i)] for i in test_idx]
        test_dataset = Dataset(data=test_data, transform=test_transform)
        test_loader = DataLoader(test_dataset, batch_size=args.batch_size, shuffle=False,
                                 num_workers=args.num_workers, pin_memory=True)
        print(f'\n===== Fold {fold_idx} ===== test={len(test_dataset)}', end='')

        # ----- 训练集（用于阈值校准；mergeTV=True 时训练集 = train_idx 全部）-----
        train_loader_cal = None
        if do_calibrate:
            train_data_cal = [adni[int(i)] for i in train_idx]
            train_dataset_cal = Dataset(data=train_data_cal, transform=test_transform)
            train_loader_cal = DataLoader(train_dataset_cal, batch_size=args.batch_size,
                                          shuffle=False, num_workers=args.num_workers,
                                          pin_memory=True)
            print(f'  train(cal)={len(train_dataset_cal)}')

        # 收集所有目录的预测
        probs_by_dir = {}          # {exp_dir: {snapshot_epoch: test_prob}}
        train_probs_by_dir = {}     # {exp_dir: {snapshot_epoch: train_prob}}
        labels_ref = None
        train_labels_ref = None
        model = build_model(args, device)
        for d in exp_dirs:
            fold_dir = os.path.join(d, str(fold_idx))
            # single: best_ckpt
            best_pt = os.path.join(fold_dir, 'best_ckpt.pt')
            if not os.path.isfile(best_pt):
                print(f'  [WARN] missing {best_pt}, skip dir')
                continue
            load_state_dict_flexible(model, best_pt, device)
            p_single, y = predict_probs(model, test_loader, device, tta_factors=tta_factors)
            if labels_ref is None:
                labels_ref = y
            else:
                assert np.array_equal(labels_ref, y), f'标签顺序不一致: {d} fold {fold_idx}'
            results.setdefault('single', []).append((fold_idx, d,
                                                     cal_metrics(labels_ref, p_single)))
            print(f"[single   {os.path.basename(d):20s}] auc={results['single'][-1][2]['auc']:.4f} "
                  f"acc={results['single'][-1][2]['acc']:.4f}")

            # best_ckpt 也预测 train（用于校准）
            if do_calibrate and train_loader_cal is not None:
                p_train_single, y_train = predict_probs(model, train_loader_cal, device, tta_factors=tta_factors)
                if train_labels_ref is None:
                    train_labels_ref = y_train

            # snapshots
            probs_by_dir[d] = {}
            train_probs_by_dir[d] = {}
            for e in snapshot_epochs:
                sp = os.path.join(fold_dir, f'snapshot_epoch{e}.pt')
                if not os.path.isfile(sp):
                    print(f'  [WARN] snapshot missing: {sp}')
                    continue
                load_state_dict_flexible(model, sp, device)
                p_snap, _ = predict_probs(model, test_loader, device, tta_factors=tta_factors)
                probs_by_dir[d][e] = p_snap

                if do_calibrate and train_loader_cal is not None:
                    p_train_snap, _ = predict_probs(model, train_loader_cal, device, tta_factors=tta_factors)
                    train_probs_by_dir[d][e] = p_train_snap

            # snap_ens: 单目录内平均
            if probs_by_dir[d]:
                p_ens = np.mean(list(probs_by_dir[d].values()), axis=0)
                m = cal_metrics(labels_ref, p_ens)
                results.setdefault('snap_ens', []).append((fold_idx, d, m))
                print(f"[snap_ens {os.path.basename(d):20s}] auc={m['auc']:.4f} acc={m['acc']:.4f} "
                      f"(n_snap={len(probs_by_dir[d])})")

        # multi_seed: 所有目录所有快照联合平均（校准概率在此暂存，循环结束后统一处理）
        all_probs = [p for d_probs in probs_by_dir.values() for p in d_probs.values()]
        if len(exp_dirs) > 1 and all_probs:
            p_multi = np.mean(all_probs, axis=0)
            m = cal_metrics(labels_ref, p_multi)
            results.setdefault('multi_seed', []).append((fold_idx, 'MULTI', m))
            print(f"[multi_seed{'':15s}] auc={m['auc']:.4f} acc={m['acc']:.4f} "
                  f"(n_models={len(all_probs)})")

            if do_calibrate and train_probs_by_dir:
                all_train_probs = [p for d_probs in train_probs_by_dir.values()
                                   for p in d_probs.values()]
                if all_train_probs and train_labels_ref is not None:
                    # 收集各模型独立概率用于 Stacking（方案 G）
                    individual_test = [p for d_probs in probs_by_dir.values()
                                       for p in d_probs.values()]
                    individual_train = [p for d_probs in train_probs_by_dir.values()
                                        for p in d_probs.values()]
                    fold_records.append(dict(
                        fold=fold_idx,
                        y_test=labels_ref.copy(),
                        p_test=p_multi.copy(),
                        y_train=train_labels_ref.copy(),
                        p_train=np.mean(all_train_probs, axis=0).copy(),
                        p_test_individual=individual_test,
                        p_train_individual=individual_train,
                    ))

        del model
        torch.cuda.empty_cache()

    # ===== 方案 G: Stacking 元学习器 + AUC 加权集成（纯后处理）=====
    if use_stacking and fold_records:
        stacking_train_probs = []   # 用于全局阈值校准
        stacking_train_labels = []
        for rec in fold_records:
            X_train = np.column_stack(rec['p_train_individual'])
            X_test = np.column_stack(rec['p_test_individual'])
            y_train = rec['y_train']
            y_test = rec['y_test']

            # Stacking: LogisticRegression 元学习器
            meta_clf = LogisticRegression(C=args.stacking_C, max_iter=1000, random_state=42)
            meta_clf.fit(X_train, y_train)
            p_stack_test = meta_clf.predict_proba(X_test)[:, 1]
            p_stack_train = meta_clf.predict_proba(X_train)[:, 1]
            m = cal_metrics(y_test, p_stack_test)
            results.setdefault('stacking', []).append((rec['fold'], 'STACK', m))
            print(f"[stacking{'':18s}] auc={m['auc']:.4f} acc={m['acc']:.4f} "
                  f"(n_features={X_train.shape[1]}, C={args.stacking_C})")

            stacking_train_probs.append(p_stack_train)
            stacking_train_labels.append(y_train)

        # Stacking + 全局 Youden 阈值
        y_train_all_stack = np.concatenate(stacking_train_labels)
        p_train_all_stack = np.concatenate(stacking_train_probs)
        t_stack_global, j_stack_global = find_youden_threshold(
            y_train_all_stack, p_train_all_stack,
            thresh_min=args.thresh_min, thresh_max=args.thresh_max)
        for i, rec in enumerate(fold_records):
            p_stack_test = results['stacking'][i][2]  # 已有 0.5 阈值的 metrics
            # 重新用全局阈值计算
            # 需要重新获取 stacking test prob——从 predict_proba 获取
            X_test = np.column_stack(rec['p_test_individual'])
            X_train = np.column_stack(rec['p_train_individual'])
            meta_clf = LogisticRegression(C=args.stacking_C, max_iter=1000, random_state=42)
            meta_clf.fit(X_train, rec['y_train'])
            p_stack_test = meta_clf.predict_proba(X_test)[:, 1]
            m = cal_metrics(rec['y_test'], p_stack_test, threshold=t_stack_global)
            m['threshold'] = t_stack_global
            results.setdefault('stacking_cal_global', []).append((rec['fold'], 'STACK_CAL', m))

        # AUC 加权集成（简单替代方案）
        for rec in fold_records:
            train_aucs = [roc_auc_score(rec['y_train'], p)
                          for p in rec['p_train_individual']]
            temp = 10.0
            w = np.exp(np.array(train_aucs) * temp)
            w = w / w.sum()
            p_weighted = np.zeros_like(rec['p_test'])
            for wi, pi in zip(w, rec['p_test_individual']):
                p_weighted += wi * pi
            m = cal_metrics(rec['y_test'], p_weighted)
            results.setdefault('auc_weighted', []).append((rec['fold'], 'AUC_W', m))

        print(f"\n--- Stacking 全局阈值 ---")
        print(f"  Global threshold={t_stack_global:.4f}  (J={j_stack_global:.4f})")

    # ===== 延迟校准（纯后处理，无前向开销）=====
    if fold_records:
        # 模式1: per-fold Youden（无约束，参考用）
        for rec in fold_records:
            t, j = find_youden_threshold(rec['y_train'], rec['p_train'])
            m = cal_metrics(rec['y_test'], rec['p_test'], threshold=t)
            m['threshold'] = t
            results.setdefault('multi_seed_cal', []).append((rec['fold'], 'MULTI_CAL', m))

        # 模式2: per-fold Youden + 范围约束
        for rec in fold_records:
            t, j = find_youden_threshold(rec['y_train'], rec['p_train'],
                                         thresh_min=args.thresh_min, thresh_max=args.thresh_max)
            m = cal_metrics(rec['y_test'], rec['p_test'], threshold=t)
            m['threshold'] = t
            results.setdefault('multi_seed_cal_range', []).append((rec['fold'], 'MULTI_CAL_RANGE', m))

        # 模式3: 全局 Youden（合并所有 fold 训练集概率 → 单一阈值）
        y_train_all = np.concatenate([r['y_train'] for r in fold_records])
        p_train_all = np.concatenate([r['p_train'] for r in fold_records])
        t_global, j_global = find_youden_threshold(y_train_all, p_train_all,
                                                   thresh_min=args.thresh_min,
                                                   thresh_max=args.thresh_max)
        for rec in fold_records:
            m = cal_metrics(rec['y_test'], rec['p_test'], threshold=t_global)
            m['threshold'] = t_global
            results.setdefault('multi_seed_cal_global', []).append((rec['fold'], 'MULTI_CAL_GLOBAL', m))

    # ===== 汇总 =====
    print('\n' + '=' * 70)
    print('Final Results (5-fold mean ± std)')
    print('=' * 70)
    headers = ['acc', 'sen', 'spe', 'f1', 'auc']
    summary = {}
    for mode, rows in results.items():
        metrics = [r[2] for r in rows]
        line = f'{mode:22s}: '
        stats = []
        for h in headers:
            vals = np.array([m[h] for m in metrics])
            line += f'{h}={vals.mean():.4f}±{vals.std():.4f}  '
            stats.append((vals.mean(), vals.std()))
        print(line)
        summary[mode] = stats

    # 打印校准阈值明细
    for mode in ('multi_seed_cal', 'multi_seed_cal_range'):
        if mode in results:
            label = 'per-fold Youden' if mode == 'multi_seed_cal' else f'per-fold Youden [{args.thresh_min},{args.thresh_max}]'
            print(f'\n--- 校准阈值: {label} ---')
            for fold_idx, _, m in results[mode]:
                print(f'  Fold {fold_idx}: threshold={m.get("threshold", 0.5):.4f}')
    if 'multi_seed_cal_global' in results:
        print(f'\n--- 校准阈值: 全局 Youden (multi_seed) ---')
        print(f'  Global threshold={t_global:.4f}  (J={j_global:.4f}, n_train={len(y_train_all)})')

    if _str2bool(args.save_csv):
        csv_path = os.path.join(os.path.dirname(exp_dirs[0].rstrip('/\\')) or '.', args.out_name)
        try:
            with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
                w = csv.writer(f)
                header = ['fold', 'exp', 'mode'] + headers + ['threshold']
                w.writerow(header)
                for mode, rows in results.items():
                    for fold_idx, d, m in rows:
                        w.writerow([f'fold_{fold_idx}', os.path.basename(d.rstrip("/\\")), mode]
                                   + [f'{m[h]:.6f}' for h in headers]
                                   + [f'{m.get("threshold", 0.5):.6f}'])
                w.writerow([])
                for mode, stats in summary.items():
                    threshs = [r[2].get('threshold', 0.5) for r in results[mode]]
                    w.writerow(['mean±std', '', mode] + [f'{mm:.6f}±{ss:.6f}' for mm, ss in stats]
                               + [f'{np.mean(threshs):.6f}±{np.std(threshs):.6f}'])
            print(f'\n[CSV] saved -> {csv_path}')
        except Exception as e:
            print(f'[CSV] save failed: {e}')


def _str2bool(s):
    if isinstance(s, bool):
        return s
    return str(s).strip().lower() in ('1', 'true', 'yes', 'y', 't')


if __name__ == '__main__':
    main()
