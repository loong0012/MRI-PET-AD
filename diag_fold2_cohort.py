"""
队列/扫描仪代际混杂验证：
  - ADNI Subject 编号 Sxxxx：低编号≈ADNI1 (1.5T MRI)，高编号≈ADNI2/GO (3T MRI)
  - 检查全体及每 fold 内 队列×标签 的列联关系
  - 检查 fold2 共识错误样本的队列归属
  - 重复 PET 的两个被试标签与 fold 归属
"""
import os
os.environ['KMP_DUPLICATE_LIB_OK'] = 'TRUE'
import re
import numpy as np
import pandas as pd
from datasets.ADNI import ADNI
from utils.utils import stratified_kfold_indices

DATAROOT = './datasets/MRI PET图像'


def scode(sub):
    m = re.match(r'sub-ADNI(\d{3})S(\d+)', str(sub))
    return (m.group(1), int(m.group(2))) if m else ('???', -1)


def main():
    adni = ADNI(dataroot=DATAROOT, label_filename='ADNI.csv', task='ADCN').data_dict
    labels = [int(d['label']) for d in adni]
    subs = [d['Subject'] for d in adni]
    codes = np.array([scode(s)[1] for s in subs])

    # 队列分界：S2000 以下 ADNI1，S2000+ ADNI2/GO（ADNI 受试者号大致按入组期顺序分配）
    cohort = np.where(codes < 2000, 'ADNI1(1.5T)', np.where(codes < 4000, 'mid', 'ADNI2+(3T)'))

    print('=' * 78)
    print('1. 全体 668 样本：队列 × 标签')
    print('=' * 78)
    df = pd.DataFrame({'cohort': cohort, 'label': ['AD' if l == 1 else 'CN' for l in labels]})
    ct = pd.crosstab(df['cohort'], df['label'])
    ct['AD_rate'] = ct.get('AD', 0) / ct.sum(axis=1)
    print(ct)

    print('\n' + '=' * 78)
    print('2. 各 fold 训练集 / 测试集：队列 × 标签')
    print('=' * 78)
    splits = list(stratified_kfold_indices(labels, n_splits=5, seed=42))
    for fi, (train_idx, test_idx) in enumerate(splits):
        for name, idx in [('TRAIN', train_idx), ('TEST', test_idx)]:
            c = cohort[idx]
            l = np.array(labels)[idx]
            n1_ad = ((c == 'ADNI1(1.5T)') & (l == 1)).sum()
            n1_cn = ((c == 'ADNI1(1.5T)') & (l == 0)).sum()
            n2_ad = ((c == 'ADNI2+(3T)') & (l == 1)).sum()
            n2_cn = ((c == 'ADNI2+(3T)') & (l == 0)).sum()
            nm_ad = ((c == 'mid') & (l == 1)).sum()
            nm_cn = ((c == 'mid') & (l == 0)).sum()
            print(f'fold{fi} {name:5s}: ADNI1 AD={n1_ad:3d} CN={n1_cn:3d} (AD率={n1_ad/max(1,n1_ad+n1_cn):.2f}) | '
                  f'mid AD={nm_ad} CN={nm_cn} | ADNI2+ AD={n2_ad:3d} CN={n2_cn:3d} (AD率={n2_ad/max(1,n2_ad+n2_cn):.2f})')
        print()

    print('=' * 78)
    print('3. Fold 2 共识错误样本的队列归属')
    print('=' * 78)
    suspects = {
        'sub-ADNI018S5240': 'AD', 'sub-ADNI135S4676': 'AD', 'sub-ADNI016S5057': 'AD',
        'sub-ADNI135S4954': 'AD', 'sub-ADNI023S5120': 'AD', 'sub-ADNI003S4892': 'AD',
        'sub-ADNI098S4506': 'CN', 'sub-ADNI109S1013': 'CN', 'sub-ADNI941S1202': 'CN',
        'sub-ADNI013S0575': 'CN', 'sub-ADNI094S0489': 'CN', 'sub-ADNI027S5110': 'CN',
    }
    _, test_idx_2 = splits[2]
    test_subs_2 = set(subs[i] for i in test_idx_2)
    for s, lab in suspects.items():
        site, code = scode(s)
        coh = 'ADNI1(1.5T)' if code < 2000 else ('mid' if code < 4000 else 'ADNI2+(3T)')
        in_f2 = s in test_subs_2
        print(f'  {s}  CSV={lab}  S码={code:5d}  队列={coh}  在fold2={in_f2}')

    print('\n' + '=' * 78)
    print('4. 重复 PET 被试核查')
    print('=' * 78)
    dup = ['sub-ADNI094S4649', 'sub-ADNI127S5185']
    for s in dup:
        if s in subs:
            i = subs.index(s)
            site, code = scode(s)
            fi = next((f for f, (tr, te) in enumerate(splits) if i in te), '?')
            fi_tr = next((f for f, (tr, te) in enumerate(splits) if i in tr), '?')
            print(f'  {s}: 标签={"AD" if labels[i]==1 else "CN"} 站点={site} S码={code} '
                  f'在fold{fi}为测试集 (训练于fold{fi_tr})')
        else:
            print(f'  {s}: 不在 ADCN 任务集（可能为 MCI）')

    print('\n' + '=' * 78)
    print('5. Fold2 测试集中两队列的 AD 比例对比训练集')
    print('=' * 78)
    tr2, te2 = splits[2]
    for name, idx in [('TRAIN(4 folds合并)', tr2), ('TEST(fold2)', te2)]:
        c = cohort[idx]
        l = np.array(labels)[idx]
        for coh in ['ADNI1(1.5T)', 'mid', 'ADNI2+(3T)']:
            m = c == coh
            if m.sum() > 0:
                print(f'  {name:18s} {coh}: n={m.sum():3d}, AD率={l[m].mean():.3f}')
        print()


if __name__ == '__main__':
    main()
