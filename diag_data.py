"""
诊断脚本：定位 "训练 acc≈0.49 / sensitivity=0 / AUC<0.5" 根因

检查项：
1. raw .nii 数据强度分布（不做任何 transform）
2. ApplyScaleIntensityd 后强度分布
3. Apply NormalizeIntensityd(z-score) 后强度分布 + 是否 NaN/全零/极端值
4. 模型 forward 初始 logits 分布（argmax 是否全为同一类 → 解释 sens=0）
5. sNet 各层中间输出统计（BN3d 在 batch_size=2 下是否异常）
6. ce_loss 初始数值
"""
import os
os.environ['KMP_DUPLICATE_LIB_OK'] = 'TRUE'

import sys
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.nn import AdaptiveAvgPool1d, AdaptiveMaxPool3d
from einops.layers.torch import Rearrange

# Windows MONAI seed 溢出修复（必须在 monai transform 之前）
try:
    import monai.transforms.transform as _m_tfm
    _SAFE_MAX = 2 ** 31
    _m_tfm.MAX_SEED = _SAFE_MAX
    import sys as _sys
    for _n, _m in list(_sys.modules.items()):
        if _n.startswith("monai") and hasattr(_m, "MAX_SEED"):
            try:
                setattr(_m, "MAX_SEED", _SAFE_MAX)
            except Exception:
                pass
except Exception:
    pass

from datasets.ADNI import ADNI, ADNI_transform
from models.mymodel import model_ad
from utils.utils import build_loss, get_dataset_weights


def _stats(name, t):
    """打印 tensor 的关键统计量"""
    if not torch.is_tensor(t):
        t = torch.as_tensor(t)
    t_f = t.float().flatten()
    n_nan = int(torch.isnan(t_f).sum())
    n_inf = int(torch.isinf(t_f).sum())
    n_zero = int((t_f == 0).sum())
    ratio_zero = n_zero / max(1, t_f.numel())
    print(f"  {name}: shape={tuple(t.shape)} "
          f"mean={t_f.mean().item():.4f} std={t_f.std().item():.4f} "
          f"min={t_f.min().item():.4f} max={t_f.max().item():.4f} "
          f"#NaN={n_nan} #Inf={n_inf} zero_ratio={ratio_zero:.3f}")


def main():
    device = torch.device('cuda:0' if torch.cuda.is_available() else 'cpu')
    print(f"[device] {device}")

    dataroot = './datasets/MRI PET图像'
    adni = ADNI(dataroot=dataroot, label_filename='ADNI.csv', task='ADCN')
    data = adni.data_dict
    print(f"[ADNI] total valid samples: {len(data)}")

    # 找 2 个 CN(label=0) + 2 个 AD(label=1)
    cn_samples = [d for d in data if d['label'] == 0][:2]
    ad_samples = [d for d in data if d['label'] == 1][:2]
    samples = cn_samples + ad_samples
    labels = [int(d['label']) for d in samples]
    print(f"[samples] picked 4: labels={labels} (前2=CN, 后2=AD)")
    for s in samples:
        print(f"  Subject={s['Subject']} label={s['label']} age={s['age']:.1f}")

    # ---------- 1. 检查 test_transform (z-score) 后的 tensor ----------
    print("\n" + "=" * 70)
    print("[1] 检查 test_transform (ScaleIntensityd + NormalizeIntensityd) 输出")
    print("=" * 70)
    _, test_transform = ADNI_transform(aug='False')
    for s in samples:
        batch = test_transform(s)
        mri = batch['MRI']
        pet = batch['PET']
        print(f"\n  -- Subject {s['Subject']} (label={s['label']}) --")
        _stats("MRI", mri)
        _stats("PET", pet)
        # 非零区域强度（脑区）单独统计
        mri_nz = mri[mri != 0]
        pet_nz = pet[pet != 0]
        if mri_nz.numel() > 0:
            print(f"    MRI nonzero voxels: {mri_nz.numel()} ({mri_nz.numel()/mri.numel()*100:.1f}%) "
                  f"mean={mri_nz.float().mean().item():.4f} std={mri_nz.float().std().item():.4f}")
        if pet_nz.numel() > 0:
            print(f"    PET nonzero voxels: {pet_nz.numel()} ({pet_nz.numel()/pet.numel()*100:.1f}%) "
                  f"mean={pet_nz.float().mean().item():.4f} std={pet_nz.float().std().item():.4f}")

    # ---------- 2. 检查 raw .nii 原始强度分布 ----------
    print("\n" + "=" * 70)
    print("[2] 检查 raw .nii 原始强度分布（不做任何 transform）")
    print("=" * 70)
    import nibabel as nib
    for s in samples:
        try:
            mri_img = nib.load(s['MRI'])
            pet_img = nib.load(s['PET'])
            mri_arr = np.asarray(mri_img.get_fdata(), dtype=np.float32)
            pet_arr = np.asarray(pet_img.get_fdata(), dtype=np.float32)
            print(f"\n  -- Subject {s['Subject']} (label={s['label']}) --")
            print(f"    MRI raw: shape={mri_arr.shape} dtype={mri_img.get_data_dtype()} "
                  f"min={mri_arr.min():.4f} max={mri_arr.max():.4f} "
                  f"mean={mri_arr.mean():.4f} std={mri_arr.std():.4f}")
            print(f"    PET raw: shape={pet_arr.shape} dtype={pet_img.get_data_dtype()} "
                  f"min={pet_arr.min():.4f} max={pet_arr.max():.4f} "
                  f"mean={pet_arr.mean():.4f} std={pet_arr.std():.4f}")
        except Exception as e:
            print(f"  [raw load failed] Subject={s['Subject']}: {e}")

    # ---------- 3. 模型 forward 初始 logits ----------
    print("\n" + "=" * 70)
    print("[3] 模型 forward 初始 logits 分布（batch_size=4，未训练）")
    print("=" * 70)

    dim = 128
    net = model_ad(dim=dim, depth=3, heads=4, dim_head=dim // 4,
                   mlp_dim=dim * 4, dropout=0.15).to(device)
    net.eval()

    # 准备 batch
    mri_batch = torch.stack([test_transform(s)['MRI'] for s in samples], dim=0).to(device)
    pet_batch = torch.stack([test_transform(s)['PET'] for s in samples], dim=0).to(device)
    label_batch = torch.tensor(labels, dtype=torch.long, device=device)
    print(f"  mri_batch: {tuple(mri_batch.shape)} dtype={mri_batch.dtype}")
    print(f"  pet_batch: {tuple(pet_batch.shape)} dtype={pet_batch.dtype}")
    print(f"  label_batch: {label_batch.tolist()}")

    with torch.no_grad():
        out_logits, d_mri, d_pet = net(mri_batch, pet_batch)
    print(f"\n  output_logits shape: {tuple(out_logits.shape)}")
    print(f"  output_logits:")
    print(f"    {out_logits.cpu().numpy()}")
    probs = F.softmax(out_logits, dim=1)
    preds = out_logits.argmax(dim=1)
    print(f"  softmax probs:\n    {probs.cpu().numpy()}")
    print(f"  argmax preds: {preds.cpu().tolist()}  (真实 label: {labels})")
    n_pred_ad = int((preds == 1).sum())
    n_pred_cn = int((preds == 0).sum())
    print(f"  预测 AD={n_pred_ad} CN={n_pred_cn}（如果全 CN 则解释了 sensitivity=0）")

    # ---------- 4. sNet 各层中间输出（对比 train vs eval mode）----------
    print("\n" + "=" * 70)
    print("[4] sNet 各层输出对比 train mode vs eval mode")
    print("=" * 70)

    class sNetHook(nn.Module):
        """复制 sNet forward，但暴露每层输出"""
        def __init__(self, snet):
            super().__init__()
            self.conv1 = snet.conv1
            self.conv2 = snet.conv2
            self.conv3 = snet.conv3
            self.conv4 = snet.conv4
        def forward(self, x):
            outs = {}
            o1 = self.conv1(x); outs['conv1_out'] = o1
            o2 = self.conv2(o1); outs['conv2_out'] = o2
            o3 = self.conv3(o2); outs['conv3_out'] = o3
            o4 = self.conv4(o3); outs['conv4_out'] = o4
            return outs

    for mode_name, mode_flag in [('eval', False), ('train', True)]:
        hook_mri = sNetHook(net.mri_cnn).to(device)
        hook_pet = sNetHook(net.pet_cnn).to(device)
        if mode_flag:
            hook_mri.train(); hook_pet.train()
        else:
            hook_mri.eval(); hook_pet.eval()
        # batch_size=2 vs 4 对比
        for bs in [2, 4]:
            sub_mri = mri_batch[:bs]
            sub_pet = pet_batch[:bs]
            print(f"\n  -- mode={mode_name} batch_size={bs} --")
            with torch.no_grad():
                mri_outs = hook_mri(sub_mri)
                pet_outs = hook_pet(sub_pet)
            print(f"  MRI sNet:")
            for k, v in mri_outs.items():
                _stats(f"    {mode_name}_bs{bs}_{k}", v)
            print(f"  PET sNet:")
            for k, v in pet_outs.items():
                _stats(f"    {mode_name}_bs{bs}_{k}", v)
            # 关键比较：train mode 下两个样本的特征是否差异巨大
            if bs == 2 and mode_flag:
                diff = (mri_outs['conv4_out'][0] - mri_outs['conv4_out'][1]).abs().mean().item()
                print(f"  [!] train mode bs=2: 样本0 vs 样本1 conv4_out 平均差异 = {diff:.4f}")

    # ---------- 4.5 对比 train vs eval 的最终 logits ----------
    print("\n" + "=" * 70)
    print("[4.5] 模型 train mode vs eval mode 的 logits 对比（核心：BN3d 差异）")
    print("=" * 70)
    # eval mode
    net.eval()
    with torch.no_grad():
        eval_logits, _, _ = net(mri_batch, pet_batch)
    print(f"\n  EVAL mode logits:\n    {eval_logits.cpu().numpy()}")
    print(f"  EVAL mode argmax: {eval_logits.argmax(1).cpu().tolist()}")

    # train mode
    net.train()
    # 跑两次看是否每次结果不同（BN3d batch=2 统计随机性）
    train_logits_runs = []
    for i in range(3):
        with torch.no_grad():
            tl, _, _ = net(mri_batch, pet_batch)
        train_logits_runs.append(tl)
    print(f"\n  TRAIN mode logits (run 0):\n    {train_logits_runs[0].cpu().numpy()}")
    print(f"  TRAIN mode argmax (run 0): {train_logits_runs[0].argmax(1).cpu().tolist()}")
    # 跑3次 train mode 看一致性
    diff_01 = (train_logits_runs[0] - train_logits_runs[1]).abs().mean().item()
    diff_02 = (train_logits_runs[0] - train_logits_runs[2]).abs().mean().item()
    print(f"  TRAIN mode 跑3次 logits 差异: run0vs1={diff_01:.4f} run0vs2={diff_02:.4f}")
    print(f"  [!] 差异大说明 BN3d 在 batch_size=4 下随机性极强（无 dropout 仍不稳）")

    # 评估 eval vs train logits 差距
    diff_te = (eval_logits - train_logits_runs[0]).abs().mean().item()
    print(f"  EVAL vs TRAIN(logits) 差异: {diff_te:.4f}")

    # ---------- 5. ce_loss 初始值 ----------
    print("\n" + "=" * 70)
    print("[5] 初始 ce_loss 值（lsce 默认）")
    print("=" * 70)
    # 模拟 option object
    class Opt:
        loss_type = 'lsce'
        use_class_weight = 'True'
        label_smooth = 0.05
        focal_gamma = 2.0
    opt = Opt()
    weights = get_dataset_weights(samples, None)
    print(f"  weights (CN, AD): {weights.tolist()}")
    criterion = build_loss(opt, class_weights=weights)
    with torch.no_grad():
        # train mode 才会通过 dropout，但 eval 也大致能看 loss 量级
        loss = criterion(out_logits, label_batch)
    print(f"  initial ce_loss (eval mode): {loss.item():.4f}")
    # 随机二分类 CE 应该 ≈ ln(2)=0.693；远低于此说明模型一开始就偏某一类

    # ---------- 6. 验证：logits 是否区分性 ----------
    print("\n" + "=" * 70)
    print("[6] logit 区分性：CN 样本 vs AD 样本的 logit[1] 均值")
    print("=" * 70)
    logit_ad_dim = out_logits[:, 1]
    cn_logits = logit_ad_dim[:2]
    ad_logits = logit_ad_dim[2:]
    print(f"  CN 样本 logit[AD]: {cn_logits.cpu().tolist()} mean={cn_logits.mean().item():.4f}")
    print(f"  AD 样本 logit[AD]: {ad_logits.cpu().tolist()} mean={ad_logits.mean().item():.4f}")
    diff = (ad_logits.mean() - cn_logits.mean()).item()
    print(f"  diff (AD - CN) = {diff:.4f}  正值说明方向对；负值/接近0说明没区分度")

    print("\n[诊断完成]")


if __name__ == '__main__':
    main()
