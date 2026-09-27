"""
Fold 2 共识错误样本的文件级核查：
  1. NIfTI 仿射/朝向检查（pipeline 无 Orientationd，朝向异常直接进入模型）
  2. MRI/PET 形状、体素间距、文件大小
  3. 全量 668×2 文件跨被试重复检查（同内容不同名=文件错配）
  4. 原始强度统计（未归一化前）
"""
import os
os.environ['KMP_DUPLICATE_LIB_OK'] = 'TRUE'

import hashlib
import numpy as np
import pandas as pd
import nibabel as nib
from collections import Counter, defaultdict

DATAROOT = './datasets/MRI PET图像'
MRI_DIR = os.path.join(DATAROOT, 'MRI')
PET_DIR = os.path.join(DATAROOT, 'PET')

# 12 个共识高置信度错误样本（errConf > 0.8，15/15 模型一致错）
SUSPECTS = {
    'sub-ADNI018S5240': 'AD', 'sub-ADNI135S4676': 'AD', 'sub-ADNI016S5057': 'AD',
    'sub-ADNI135S4954': 'AD', 'sub-ADNI023S5120': 'AD', 'sub-ADNI003S4892': 'AD',
    'sub-ADNI098S4506': 'CN', 'sub-ADNI109S1013': 'CN', 'sub-ADNI941S1202': 'CN',
    'sub-ADNI013S0575': 'CN', 'sub-ADNI094S0489': 'CN', 'sub-ADNI027S5110': 'CN',
}


def find_file(d, sub):
    for ext in ('.nii.gz', '.nii'):
        p = os.path.join(d, sub + ext)
        if os.path.isfile(p):
            return p
    return None


def file_hash(path, chunk=1 << 20):
    h = hashlib.md5()
    with open(path, 'rb') as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def aff_summary(aff):
    """从仿射提取朝向签名：旋转部分 3x3 的符号 + 体素间距"""
    rot = aff[:3, :3]
    signs = tuple(np.sign(rot[np.argmax(np.abs(rot), axis=0), range(3)]).astype(int))
    spacing = tuple(np.abs(rot.diagonal() if np.allclose(rot[np.arange(3), np.argmax(np.abs(rot), axis=0)], rot[np.arange(3), np.arange(3)]) else np.abs(rot[np.argmax(np.abs(rot), axis=0), range(3)])))
    return signs, np.round(np.abs(np.diag(rot)), 3).tolist()


def main():
    csv = pd.read_csv(os.path.join(DATAROOT, 'ADNI.csv'))
    label_map = dict(zip(csv['Subject'], csv['Group']))

    # ---------- 1. 全体被试朝向统计 + 重复文件 ----------
    print('=' * 80)
    print('1. 全量 NIfTI 朝向签名统计（affine 主轴符号）')
    print('=' * 80)
    mri_ori = Counter()
    pet_ori = Counter()
    mri_hash_map = defaultdict(list)
    pet_hash_map = defaultdict(list)
    mri_meta = {}
    for sub in sorted(set(csv['Subject'])):
        mp, pp = find_file(MRI_DIR, sub), find_file(PET_DIR, sub)
        if not (mp and pp):
            continue
        for path, ori_ctr, hmap, meta in (
                (mp, mri_ori, mri_hash_map, mri_meta),
                (pp, pet_ori, pet_hash_map, {})):
            try:
                img = nib.load(path)
                signs, spacing = aff_summary(img.affine)
                ori_ctr[signs] += 1
                hmap[file_hash(path)].append(sub)
                if meta is not None:
                    data = img.get_fdata()
                    meta[sub] = dict(shape=img.shape, spacing=spacing,
                                    orient=signs, fsize=os.path.getsize(path))
            except Exception as e:
                print(f'  [ERR] {sub}: {e}')

    print('MRI 朝向分布:')
    for k, v in mri_ori.most_common():
        print(f'  {k}: {v}')
    print('PET 朝向分布:')
    for k, v in pet_ori.most_common():
        print(f'  {k}: {v}')

    print('\n' + '=' * 80)
    print('2. 跨被试重复文件检查（同 hash 不同 subject）')
    print('=' * 80)
    dup_found = 0
    for hmap, mod in ((mri_hash_map, 'MRI'), (pet_hash_map, 'PET')):
        for h, subs in hmap.items():
            if len(set(subs)) > 1:
                dup_found += 1
                print(f'  [{mod}] 重复! subjects={sorted(set(subs))}')
    if dup_found == 0:
        print('  未发现跨被试重复文件 ✅')

    # ---------- 3. 嫌疑样本明细 ----------
    print('\n' + '=' * 80)
    print('3. 12 个共识错误样本明细')
    print('=' * 80)
    print(f'{"subject":<20}{"csv":>4}{"img":>4} {"MRI shape":>16} {"MRI ori":>22} '
          f'{"PET shape":>16} {"PET ori":>22}  fsize_MB')
    suspect_mri = {}
    for sub in SUSPECTS:
        mp, pp = find_file(MRI_DIR, sub), find_file(PET_DIR, sub)
        csv_grp = label_map.get(sub, '?')
        if not (mp and pp):
            print(f'{sub:<20}{csv_grp:>4}{"MISSING":>4}')
            continue
        m_img, p_img = nib.load(mp), nib.load(pp)
        m_signs, m_sp = aff_summary(m_img.affine)
        p_signs, p_sp = aff_summary(p_img.affine)
        fm = os.path.getsize(mp) / 1e6
        print(f'{sub:<20}{csv_grp:>4}{SUSPECTS[sub]:>4} {str(m_img.shape):>16} '
              f'{str(m_signs):>22} {str(p_img.shape):>16} {str(p_signs):>22}  {fm:>6.1f}')
        suspect_mri[sub] = mp

    # ---------- 4. 对照：各 fold 正确样本的朝向是否不同 ----------
    print('\n' + '=' * 80)
    print('4. 嫌疑样本 vs 全体 朝向对比')
    print('=' * 80)
    all_mri_ori = mri_ori
    suspect_ori = Counter(mri_meta[s]['orient'] for s in SUSPECTS if s in mri_meta)
    print('嫌疑样本 MRI 朝向分布:', dict(suspect_ori))
    print('全体 MRI 朝向分布:  ', dict(all_mri_ori))

    # ---------- 5. 原始强度统计（未归一化）----------
    print('\n' + '=' * 80)
    print('5. 嫌疑样本 vs 随机对照 原始强度统计')
    print('=' * 80)
    rng = np.random.RandomState(42)
    all_subs = [s for s in label_map if find_file(MRI_DIR, s) and find_file(PET_DIR, s)]
    controls = rng.choice([s for s in all_subs if s not in SUSPECTS], 8, replace=False)
    for grp, subs in [('SUSPECT', list(SUSPECTS)), ('CONTROL', list(controls))]:
        print(f'\n[{grp}]')
        for sub in subs:
            mp, pp = find_file(MRI_DIR, sub), find_file(PET_DIR, sub)
            m = np.asarray(nib.load(mp).get_fdata(), dtype=np.float32)
            p = np.asarray(nib.load(pp).get_fdata(), dtype=np.float32)
            m_nz, p_nz = m[m > 0], p[p > 0]
            print(f'  {sub:<20} ({label_map.get(sub,"?")}) '
                  f'MRI: p99={np.percentile(m_nz,99):>8.1f} mean={m_nz.mean():>7.1f} '
                  f'nz={float((m>0).mean()):.2f} | '
                  f'PET: p99={np.percentile(p_nz,99):>8.2f} mean={p_nz.mean():>7.2f} '
                  f'nz={float((p>0).mean()):.2f}')


if __name__ == '__main__':
    main()
