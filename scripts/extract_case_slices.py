# -*- coding: utf-8 -*-
"""
从数据集抽取真实被试切片，作为论文图 1 的体数据立方体表面图像。

被试：sub-ADNI002S4262（ADNI，Group = CN）
数据源：datasets/MRI PET图像/{MRI,PET}/sub-ADNI002S4262.nii

处理流程：
  1. 按 affine 判定轴位轴（该数据集为 RAS，轴位轴 = 2）
  2. 选取脑组织像素最多的轴位层
  3. 按 NIfTI zooms 做各向同性重采样（R 1.54 mm/px、A 1.83 mm/px），
     避免直接 resize 造成 19% 方向性拉伸
  4. 裁到脑组织外接框，补零成正方形，输出灰度图

输出：docs/figures/paper_fig1/assets/{mri,pet}_face.png（各 900 × 900）
注意：输出为真实去标识化被试影像，引用时请按 ADNI 数据使用条款标注来源。

用法： python scripts/extract_case_slices.py
"""

from __future__ import annotations

import os

import nibabel as nib
import numpy as np
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "datasets", "MRI PET图像")
OUT = os.path.join(ROOT, "docs", "figures", "paper_fig1", "assets")

SUBJECT = "sub-ADNI002S4262"   # Group = CN
SIDE = 900                      # 输出边长（像素）


def load_volume(modality: str) -> tuple[np.ndarray, tuple[float, ...], str]:
    path = os.path.join(DATA, modality, SUBJECT + ".nii")
    img = nib.load(path)
    vol = np.asanyarray(img.dataobj)
    if vol.ndim > 3:
        vol = vol[..., 0]
    return vol, tuple(float(z) for z in img.header.get_zooms()[:3]), \
        "".join(nib.aff2axcodes(img.affine))


def axial_index(codes: str, fallback: int = 2) -> int:
    for i, c in enumerate(codes):
        if c in ("S", "I"):
            return i
    return fallback


def norm(a: np.ndarray, lo_p: float, hi_p: float) -> np.ndarray:
    nz = a[a > 0]
    if nz.size < 16:
        lo, hi = float(a.min()), float(a.max())
    else:
        lo, hi = np.percentile(nz, lo_p), np.percentile(nz, hi_p)
    return np.clip((a - lo) / max(hi - lo, 1e-6), 0, 1)


def crop_bbox(a: np.ndarray, thr: float) -> np.ndarray:
    ys, xs = np.where(a > thr)
    if ys.size == 0:
        return a
    return a[ys.min():ys.max() + 1, xs.min():xs.max() + 1]


def pad_square(a: np.ndarray) -> np.ndarray:
    h, w = a.shape
    n = max(h, w)
    out = np.zeros((n, n), a.dtype)
    out[(n - h) // 2:(n - h) // 2 + h, (n - w) // 2:(n - w) // 2 + w] = a
    return out


def make_face(modality: str, lo_p: float, hi_p: float, thr: float,
              out_path: str) -> dict:
    vol, zooms, codes = load_volume(modality)
    ax = axial_index(codes)
    mask = vol > thr
    z = int(np.argmax(mask.sum(axis=(0, 1))))       # 脑组织面积最大的层
    sl = np.take(vol, z, axis=ax).astype(np.float32)

    sl = crop_bbox(sl, thr)
    # 各向同性重采样：把物理尺寸换成 1 mm/px 的等比网格
    #   sl 的两个轴对应 (ax0, ax1)，取非轴位轴的两个 zoom
    other = [zooms[i] for i in range(3) if i != ax]
    new_h = max(2, int(round(sl.shape[0] * other[0])))
    new_w = max(2, int(round(sl.shape[1] * other[1])))
    img = Image.fromarray((norm(sl, lo_p, hi_p) * 255).astype(np.uint8))
    img = img.resize((new_w, new_h), Image.LANCZOS)  # PIL 尺寸为 (w, h)

    a = np.rot90(np.asarray(img))                    # 转成头朝上
    a = pad_square(a)
    Image.fromarray(a).resize((SIDE, SIDE), Image.LANCZOS).save(out_path)
    return {"modality": modality, "axcodes": codes, "axial_axis": ax,
            "slice": z, "zooms": zooms, "out": os.path.relpath(out_path, ROOT)}


def main():
    os.makedirs(OUT, exist_ok=True)
    # MRI：脑组织窗宽，阈 120（该数据集中脑组织显著高于 120）
    # PET：SUV 样强度，阈 0.05
    specs = [("MRI", 0.0, 92.0, 120.0, "mri_face.png"),
             ("PET", 2.0, 99.0, 0.05, "pet_face.png")]
    print(f"抽取真实被试切片：{SUBJECT}")
    for mod, lo_p, hi_p, thr, name in specs:
        info = make_face(mod, lo_p, hi_p, thr, os.path.join(OUT, name))
        print(f"  {info['modality']:3s}  axcodes={info['axcodes']}  "
              f"轴位轴={info['axial_axis']}  层号={info['slice']}  "
              f"zooms={tuple(round(z, 2) for z in info['zooms'])}  "
              f"-> {info['out']}")
    print("完成。该影像为去标识化 ADNI 被试，引用请遵守 ADNI 数据使用条款。")


if __name__ == "__main__":
    main()
