# -*- coding: utf-8 -*-
"""
复现 TransMF 原论文方法图（ISBI 2023, Fig.1 三面板 (a)(b)(c)）。
版式、配色与全英文标注照原图；图内模块细节换成 models/mymodel.py 的真实实现；
体数据立方体表面使用真实被试切片（见 scripts/extract_case_slices.py）。

原图配色为逐像素采样所得，非目测：
    (a) 底色 #FFF5EF   (b) 底色 #FFFBEB   (c) 底色 #EFF1F5
    MRI 主色 #70AD46   填充 #A9D18F / #BBDBA5
    PET 主色 #4470C5   填充 #688FD1 / #849FD4
    所有方框均为"无填充 + 描边"——原图的图形语言即如此。

项目真实实现对应：
    sNet                      → 论文 (b) CNN Encoder，逐层一致
                                额外：每个 Conv3d 后接 InstanceNorm3d + LeakyReLU
    CrossTransformer_MOD_AVG  → 论文 (c) Transformer Encoder Layer，depth=3
                                dim=128, heads=4, dim_head=32, mlp_dim=512
    revgrad(x, α=2) + D(128→128→2) → 论文 (a) Reverse Gradient + Discriminator
    fc_cls 512→512→64→2       → 论文 (a) Prediction

用法：
    python scripts/extract_case_slices.py     # 先抽真实切片
    python scripts/plot_transmf_paper_fig1.py # 再出图
"""

from __future__ import annotations

import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import to_rgb
from matplotlib.patches import (Circle, FancyArrowPatch, FancyBboxPatch,
                                Polygon, Rectangle)
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "docs", "figures", "paper_fig1")
ASSETS = os.path.join(OUT, "assets")

# ---------------------------------------------------------------- 字体
plt.rcParams["font.family"] = ["Times New Roman", "SimSun"]
plt.rcParams["font.serif"] = ["Times New Roman", "SimSun"]
plt.rcParams["axes.unicode_minus"] = False
plt.rcParams["pdf.fonttype"] = 42
plt.rcParams["ps.fonttype"] = 42
plt.rcParams["hatch.linewidth"] = 0.5

# ---------------------------------------------------------------- 配色（原图采样值）
BG_A = "#FFF5EF"
BG_B = "#FFFBEB"
BG_C = "#EFF1F5"
GREEN = "#70AD46"
GREEN_F = "#A9D18F"
GREEN_L = "#BBDBA5"
BLUE = "#4470C5"
BLUE_F = "#688FD1"
BLUE_L = "#849FD4"
INK = "#1A1A1A"

LW_BOX = 0.7
LW_THICK = 1.3
LW_CUBE = 0.6
MS = 7.0

FS_T = 5.4
FS_S = 4.2
FS_M = 5.8

DPI = 600

# 画布 170 × 125 mm；1 x 数据单位 = 1.70 mm，1 y 数据单位 = 1.25 mm
MM_X = 1.70
MM_Y = 1.25


def new_fig(w_mm, h_mm):
    fig = plt.figure(figsize=(w_mm / 25.4, h_mm / 25.4))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 100)
    ax.axis("off")
    return fig, ax


# ---------------------------------------------------------------- 图元
def rbox(ax, x0, y0, x1, y1, ec=INK, fc="none", lw=LW_BOX, ls="-", r=0.55,
         z=4):
    ax.add_patch(FancyBboxPatch(
        (x0, y0), x1 - x0, y1 - y0,
        boxstyle=f"round,pad=0,rounding_size={r}", linewidth=lw,
        edgecolor=ec, facecolor=fc, linestyle=ls, zorder=z))


def txt(ax, x, y, s, size=FS_T, color=INK, ha="center", va="center", z=9,
        style="normal"):
    return ax.text(x, y, s, fontsize=size, color=color, ha=ha, va=va,
                   zorder=z, style=style)


def arw(ax, p0, p1, color=INK, lw=LW_THICK, ms=MS, style="-|>", z=6, ls="-"):
    ax.add_patch(FancyArrowPatch(
        p0, p1, arrowstyle=style, mutation_scale=ms, linewidth=lw,
        color=color, zorder=z, shrinkA=0, shrinkB=0, linestyle=ls,
        joinstyle="miter"))


def wire(ax, pts, color=INK, lw=LW_BOX, z=5, ls="-"):
    ax.plot([p[0] for p in pts], [p[1] for p in pts], color=color,
            linewidth=lw, zorder=z, linestyle=ls,
            solid_capstyle="butt", solid_joinstyle="miter")


def _dark(hexcolor, f=0.86):
    return "#%02X%02X%02X" % tuple(
        max(0, min(255, int(c * 255 * f))) for c in to_rgb(hexcolor))


def cube(ax, cx, cy, s, fc, ec=INK, lw=LW_CUBE, z=4):
    dx = dy = s * 0.28
    x0, y0 = cx - s / 2.0, cy - s / 2.0
    ax.add_patch(Polygon(
        [(x0, y0 + s), (x0 + dx, y0 + s + dy), (x0 + s + dx, y0 + s + dy),
         (x0 + s, y0 + s)], closed=True, facecolor=fc, edgecolor=ec,
        linewidth=lw, zorder=z))
    ax.add_patch(Polygon(
        [(x0 + s, y0), (x0 + s + dx, y0 + dy), (x0 + s + dx, y0 + s + dy),
         (x0 + s, y0 + s)], closed=True, facecolor=_dark(fc), edgecolor=ec,
        linewidth=lw, zorder=z))
    ax.add_patch(Rectangle(
        (x0, y0), s, s, facecolor=fc, edgecolor=ec, linewidth=lw,
        zorder=z + 0.1))


def cube_stack(ax, cx, cy, s, n=3, fc=GREEN_F, ec=INK, z=4):
    ds = s * 0.24
    for i in range(n - 1, -1, -1):
        cube(ax, cx + i * ds, cy + i * ds, s, fc, ec, z=z + (n - 1 - i) * 0.02)


def volume_cube(ax, cx, cy, w_mm, img, ec=INK, lw=0.7, z=4):
    """
    带真实切片表面的体数据立方体。
    w_mm 为正面物理边长（mm），据此换算出数据单位下的宽高，
    保证正面在纸面上是正方形，不产生方向性拉伸。
    """
    w = w_mm / MM_X
    h = w_mm / MM_Y
    dx, dy = w * 0.30, w * 0.30
    x0, y0 = cx - w / 2.0, cy - h / 2.0
    ax.add_patch(Polygon(
        [(x0, y0 + h), (x0 + dx, y0 + h + dy), (x0 + w + dx, y0 + h + dy),
         (x0 + w, y0 + h)], closed=True, facecolor="#D9D9D9", edgecolor=ec,
        linewidth=lw, zorder=z))
    ax.add_patch(Polygon(
        [(x0 + w, y0), (x0 + w + dx, y0 + dy), (x0 + w + dx, y0 + h + dy),
         (x0 + w, y0 + h)], closed=True, facecolor="#B8B8B8", edgecolor=ec,
        linewidth=lw, zorder=z))
    ax.imshow(img, cmap="gray", vmin=0, vmax=255, origin="upper",
              extent=[x0, x0 + w, y0, y0 + h], aspect="auto",
              interpolation="bilinear", zorder=z + 0.1)
    ax.add_patch(Rectangle((x0, y0), w, h, fill=False, edgecolor=ec,
                           linewidth=lw, zorder=z + 0.2))


def token_group(ax, x0, y0, x1, y1, color, fc, z=5):
    rbox(ax, x0, y0, x1, y1, ec=color, lw=0.8, ls=(0, (1.6, 1.3)), r=0.5,
         z=z)
    h = 1.25
    tx0, tx1 = x0 + 0.85, x1 - 0.85
    cx = (x0 + x1) / 2.0
    for y in (y1 - 2.1, y1 - 4.1, y0 + 4.1, y0 + 2.1):
        ax.add_patch(FancyBboxPatch(
            (tx0, y - h / 2), tx1 - tx0, h,
            boxstyle="round,pad=0,rounding_size=0.22", linewidth=0.6,
            edgecolor=color, facecolor=fc, zorder=z + 1))
    for k in range(3):
        ax.plot([cx], [(y0 + y1) / 2 - 0.9 + k * 0.9], marker="o", ms=1.4,
                color=color, zorder=z + 2)


def plus_node(ax, x, y, r=0.8, z=8):
    ax.add_patch(Circle((x, y), r, facecolor="white", edgecolor=INK,
                        linewidth=0.7, zorder=z))
    ax.plot([x - r * 0.45, x + r * 0.45], [y, y], color=INK, lw=0.7,
            zorder=z + 1)
    ax.plot([x, x], [y - r * 0.45, y + r * 0.45], color=INK, lw=0.7,
            zorder=z + 1)


# ================================================================ 面板 (a)
YA_MRI, YA_PET = 92.0, 66.0
YA_MID = 79.0
X_ENC0, X_ENC1 = 9.5, 21.0
X_TOK0, X_TOK1 = 34.0, 39.0
X_BUS_IN = 41.3
X_DIS0, X_DIS1 = 22.5, 35.5
Y_DIS0, Y_DIS1 = 74.5, 83.5
X_TR0, X_TR1 = 43.0, 59.0
X_BUS_OUT = 60.3
X_OTOK0, X_OTOK1 = 61.8, 66.8
X_GAP0, X_GAP1 = 69.3, 76.8
X_BAR0, X_BAR1 = 79.0, 80.5
X_FC0, X_FC1 = 82.0, 90.5
X_PRED = 96.2
VOL_MM = 8.4


def panel_a(ax, mri_img, pet_img):
    # ---------- 真实被试体数据 ----------
    for name, cy, img in (("MRI", YA_MRI, mri_img), ("PET", YA_PET, pet_img)):
        volume_cube(ax, 4.3, cy, VOL_MM, img)
        txt(ax, 4.3, cy - VOL_MM / MM_Y / 2 - 1.3, name, size=FS_M)
        arw(ax, (7.3, cy), (X_ENC0, cy))

    # ---------- 编码器 ----------
    rbox(ax, X_ENC0, YA_MRI - 5, X_ENC1, YA_MRI + 5, ec=GREEN, lw=1.0)
    txt(ax, (X_ENC0 + X_ENC1) / 2, YA_MRI, "MRI CNN\nEncoder", size=FS_T)
    rbox(ax, X_ENC0, YA_PET - 5, X_ENC1, YA_PET + 5, ec=BLUE, lw=1.0)
    txt(ax, (X_ENC0 + X_ENC1) / 2, YA_PET, "PET CNN\nEncoder", size=FS_T)

    # ---------- 特征图 + reshape + token ----------
    for cy, col, fil in ((YA_MRI, GREEN, GREEN_L), (YA_PET, BLUE, BLUE_L)):
        arw(ax, (X_ENC1, cy), (23.6, cy))
        cube_stack(ax, 25.7, cy - 1.0, 2.9, n=3,
                   fc=GREEN_F if col == GREEN else BLUE_F)
        arw(ax, (29.4, cy - 1.0), (X_TOK0, cy - 1.0))
        txt(ax, 31.8, cy + 2.6, "reshape", size=4.0)
        token_group(ax, X_TOK0, cy - 4.5, X_TOK1, cy + 4.5, col, fil)

    # ---------- Reverse Gradient + Discriminator ----------
    arw(ax, (26.2, YA_MRI - 4.6), (26.2, Y_DIS1), style="-", lw=LW_THICK)
    arw(ax, (26.2, YA_PET + 4.6), (26.2, Y_DIS0), style="-", lw=LW_THICK)
    arw(ax, (27.0, Y_DIS1 - 1.0), (X_ENC1 + 0.4, YA_MRI - 4.3),
        lw=1.1, ms=6, ls=(0, (2.0, 1.3)))
    arw(ax, (27.0, Y_DIS0 + 1.0), (X_ENC1 + 0.4, YA_PET + 4.3),
        lw=1.1, ms=6, ls=(0, (2.0, 1.3)))
    txt(ax, 14.3, Y_DIS1 + 1.7, "Reverse Gradient  revgrad(α=2)", size=3.6)
    txt(ax, 14.3, Y_DIS0 - 1.7, "Reverse Gradient  revgrad(α=2)", size=3.6)

    rbox(ax, X_DIS0, Y_DIS0, X_DIS1, Y_DIS1, ec=INK, lw=0.9, r=0.7)
    cy = (Y_DIS0 + Y_DIS1) / 2
    cx = (X_DIS0 + X_DIS1) / 2
    txt(ax, cx, cy + 2.4, "Discriminator", size=FS_T)
    txt(ax, cx, cy - 0.1, "128→128→2", size=FS_S)
    txt(ax, cx, cy - 2.5, "domain: MRI=1, PET=0", size=FS_S)

    arw(ax, (X_DIS1, cy), (X_DIS1 + 1.2, cy))
    txt(ax, 38.4, cy + 1.8, "L$_a$", size=7.5, style="italic")
    txt(ax, 38.4, cy - 1.8, "weight 0.1", size=FS_S)

    # ---------- 汇入融合模块 ----------
    wire(ax, [(X_TOK1, YA_MRI), (X_BUS_IN, YA_MRI)])
    wire(ax, [(X_TOK1, YA_PET), (X_BUS_IN, YA_PET)])
    wire(ax, [(X_BUS_IN, YA_MRI), (X_BUS_IN, YA_PET)])
    arw(ax, (X_BUS_IN, YA_MID), (X_TR0, YA_MID))

    rbox(ax, X_TR0, 62.0, X_TR1, 95.0, ec=INK, lw=1.0, r=1.5)
    cx = (X_TR0 + X_TR1) / 2
    txt(ax, cx, YA_MID + 4.0, "Multimodal Transformer", size=FS_T)
    txt(ax, cx, YA_MID + 1.4, "Encoder", size=FS_T)
    txt(ax, cx, YA_MID - 2.2, "CrossTransformer", size=FS_S)
    txt(ax, cx, YA_MID - 4.4, "MOD_AVG", size=FS_S)
    txt(ax, cx, YA_MID - 6.8, "×3 layers  dim=128  heads=4", size=FS_S)

    # ---------- 输出 token ----------
    arw(ax, (X_TR1, YA_MID), (X_BUS_OUT, YA_MID))
    wire(ax, [(X_BUS_OUT, YA_MRI), (X_BUS_OUT, YA_PET)])
    for cy_, col, fil in ((YA_MRI, GREEN, GREEN_L), (YA_PET, BLUE, BLUE_L)):
        wire(ax, [(X_BUS_OUT, cy_), (X_OTOK0, cy_)])
        token_group(ax, X_OTOK0, cy_ - 4.5, X_OTOK1, cy_ + 4.5, col, fil)

    # ---------- GAP / GMP ----------
    for yc, label in ((YA_MRI + 1.5, "GAP"), (YA_MRI - 8.0, "GMP"),
                      (YA_PET + 1.5, "GAP"), (YA_PET - 8.0, "GMP")):
        rbox(ax, X_GAP0, yc - 3.2, X_GAP1, yc + 3.2, ec=INK, lw=0.8, r=0.6)
        txt(ax, (X_GAP0 + X_GAP1) / 2, yc, label, size=FS_M)

    for cy_tok, y_gap in ((YA_MRI, YA_MRI + 1.5), (YA_PET, YA_PET + 1.5)):
        y_gmp = y_gap - 9.5
        xb = X_OTOK1 + 1.3
        wire(ax, [(X_OTOK1, cy_tok), (xb, cy_tok)])
        wire(ax, [(xb, y_gap), (xb, y_gmp)])
        arw(ax, (xb, y_gap), (X_GAP0, y_gap))
        arw(ax, (xb, y_gmp), (X_GAP0, y_gmp))

    # ---------- 拼接向量条 ----------
    ax.add_patch(FancyBboxPatch(
        (X_BAR0, 78.0), X_BAR1 - X_BAR0, 18.5,
        boxstyle="round,pad=0,rounding_size=0.3", linewidth=0.7,
        edgecolor=GREEN, facecolor=GREEN_F, zorder=5))
    ax.add_patch(FancyBboxPatch(
        (X_BAR0, 55.5), X_BAR1 - X_BAR0, 22.5,
        boxstyle="round,pad=0,rounding_size=0.3", linewidth=0.7,
        edgecolor=BLUE, facecolor=BLUE_F, zorder=5))
    for yc in (YA_MRI + 1.5, YA_MRI - 8.0, YA_PET + 1.5, YA_PET - 8.0):
        arw(ax, (X_GAP1, yc), (X_BAR0, yc), lw=1.0, ms=5)
    txt(ax, (X_BAR0 + X_BAR1) / 2, 97.2, "Concat 512-d", size=FS_S)

    # ---------- 分类头 ----------
    arw(ax, (X_BAR1, YA_MID), (X_FC0, YA_MID))
    rbox(ax, X_FC0, YA_MID - 6.5, X_FC1, YA_MID + 6.5, ec=INK, lw=0.9, r=0.7)
    cx = (X_FC0 + X_FC1) / 2
    txt(ax, cx, YA_MID + 2.8, "Classifier fc_cls", size=FS_T)
    txt(ax, cx, YA_MID + 0.2, "512→512→64→2", size=FS_S)
    txt(ax, cx, YA_MID - 2.4, "LN+ReLU+Dropout", size=FS_S)

    arw(ax, (X_FC1, YA_MID), (X_FC1 + 2.2, YA_MID))
    txt(ax, X_PRED, YA_MID, "Prediction", size=FS_M)
    arw(ax, (X_PRED, YA_MID - 3.4), (X_PRED, YA_MID - 7.6))
    txt(ax, X_PRED, YA_MID - 10.0, "L$_c$", size=7.5, style="italic")


def panel_a_regions(ax):
    y0, y1 = 48.6, 53.4
    ax.add_patch(Rectangle((0.6, y0), 98.8, y1 - y0, facecolor="#FFFFFF",
                           edgecolor="none", zorder=1))
    dash = dict(color="#B9B4AE", lw=0.6, ls=(0, (1.4, 1.2)), z=2)
    wire(ax, [(0.6, y1), (99.4, y1)], **dash)
    wire(ax, [(0.6, y0), (99.4, y0)], **dash)
    for x in (42.5, 66.0):
        wire(ax, [(x, y0), (x, y1)], **dash)
    yc = (y0 + y1) / 2
    txt(ax, 21.5, yc, "Feature Extraction", size=5.6)
    txt(ax, 54.0, yc, "Feature Fusion", size=5.6)
    txt(ax, 83.0, yc, "Classification Head", size=5.6)
    txt(ax, 1.9, 97.2, "(a)", size=6.6)


# ================================================================ 面板 (b)
def panel_b(ax):
    ax.add_patch(Rectangle((0.5, 0.5), 49.0, 47.0, facecolor=BG_B,
                           edgecolor="none", zorder=0))
    txt(ax, 1.9, 45.6, "(b)", size=6.6)
    txt(ax, 11.0, 45.6, "Input Image", size=FS_T)

    rbox(ax, 4.0, 3.5, 46.0, 43.5, ec=INK, lw=0.8, ls=(0, (1.6, 1.3)), r=1.3)

    col1 = ["Conv, 32@3×3×3", "Maxpool, 2×2×2", "Conv, 32@3×3×3",
            "Conv, 64@3×3×3", "Maxpool, 2×2×2"]
    col2 = ["Conv, 64@3×3×3", "Conv, 128@3×3×3", "Maxpool, 2×2×2",
            "Conv, 256@3×3×3", "Conv, 128@1×1×1", "Avgpool, 2×2×2"]
    bx0a, bx1a = 5.5, 24.5
    bx0b, bx1b = 25.5, 45.0
    h, gap = 4.4, 0.75
    cy_col = 23.5

    def draw_col(labels, x0, x1):
        total = len(labels) * h + (len(labels) - 1) * gap
        ytop = cy_col + total / 2.0
        for i, lab in enumerate(labels):
            y1 = ytop - i * (h + gap)
            y0 = y1 - h
            rbox(ax, x0, y0, x1, y1, ec=INK, lw=0.7, r=0.5)
            if lab.startswith("Conv"):
                txt(ax, (x0 + x1) / 2, y1 - 1.5, lab, size=FS_S)
                txt(ax, (x0 + x1) / 2, y0 + 1.15, "IN+LReLU", size=4.0,
                    color="#6B6B6B")
            else:
                txt(ax, (x0 + x1) / 2, (y0 + y1) / 2, lab, size=FS_S)
        return ytop, ytop - total

    _, c1_bot = draw_col(col1, bx0a, bx1a)
    c2_top, c2_bot = draw_col(col2, bx0b, bx1b)

    arw(ax, (11.0, 44.6), (11.0, cy_col + (len(col1) * h + (len(col1) - 1)
                                           * gap) / 2))

    wire(ax, [(15.0, c1_bot), (15.0, 5.2), (24.9, 5.2), (24.9, 42.2),
              (35.25, 42.2)])
    arw(ax, (35.25, 42.2), (35.25, c2_top))

    arw(ax, (35.25, c2_bot), (35.25, 2.3))
    txt(ax, 35.25, 1.2, "Feature maps", size=FS_T)


# ================================================================ 面板 (c)
def panel_c(ax):
    ax.add_patch(Rectangle((50.5, 0.5), 49.0, 47.0, facecolor=BG_C,
                           edgecolor="none", zorder=0))
    txt(ax, 51.9, 45.6, "(c)", size=6.6)

    XT0, XT1 = 51.5, 55.5
    BX0, BX1 = 57.5, 93.0
    XO0, XO1 = 94.5, 98.5
    TOP_C, BOT_C = 34.5, 13.5
    TOP_Y0, TOP_Y1 = 24.5, 44.5
    BOT_Y0, BOT_Y1 = 3.5, 23.5

    txt(ax, 75.0, 46.1, "Transformer Encoder Layer  × 3", size=FS_T)
    txt(ax, 75.0, 1.6, "Transformer Encoder Layer  × 3", size=FS_T)

    for cy, col, fil, y0, y1, tag in (
            (TOP_C, GREEN, GREEN_L, TOP_Y0, TOP_Y1, "M"),
            (BOT_C, BLUE, BLUE_L, BOT_Y0, BOT_Y1, "P")):
        token_group(ax, XT0, cy - 4.5, XT1, cy + 4.5, col, fil)
        token_group(ax, XO0, cy - 4.5, XO1, cy + 4.5, col, fil)
        txt(ax, (XT0 + XT1) / 2, cy + 6.0,
            "MRI features" if tag == "M" else "PET features", size=4.0)
        rbox(ax, BX0, y0, BX1, y1, ec=INK, lw=0.8, ls=(0, (1.6, 1.3)), r=1.1)

        wire(ax, [(XT1, cy), (58.6, cy)])
        rbox(ax, 58.6, cy - 5.5, 62.0, cy + 5.5, ec=INK, lw=0.7, r=0.5)
        txt(ax, 60.3, cy, "Norm", size=FS_S)
        arw(ax, (62.0, cy), (64.5, cy), lw=0.7, ms=4.5)

        qkv = (["Q$_M$", "K$_M$", "V$_M$"] if tag == "M"
               else ["V$_P$", "K$_P$", "Q$_P$"])
        ys = [cy + 3.5, cy, cy - 3.5]
        for lab, yy in zip(qkv, ys):
            rbox(ax, 64.5, yy - 1.5, 69.0, yy + 1.5, ec=INK, lw=0.7, r=0.4)
            txt(ax, 66.75, yy, lab, size=FS_S)

        rbox(ax, 73.5, cy - 6.5, 78.5, cy + 6.5, ec=INK, lw=0.7, r=0.5)
        txt(ax, 76.0, cy, "Scaled Dot\nAttention", size=3.9)

        plus_node(ax, 81.0, cy)
        rbox(ax, 83.5, cy - 5.5, 87.0, cy + 5.5, ec=INK, lw=0.7, r=0.5)
        txt(ax, 85.25, cy, "Norm", size=FS_S)
        rbox(ax, 88.5, cy - 5.5, 91.5, cy + 5.5, ec=INK, lw=0.7, r=0.5)
        txt(ax, 90.0, cy, "MLP", size=FS_S)
        plus_node(ax, 92.0, cy)
        arw(ax, (92.8, cy), (XO0, cy), lw=0.8, ms=5.5)

        wire(ax, [(58.3, cy), (58.3, y1 - 1.7), (81.0, y1 - 1.7),
                  (81.0, cy + 0.8)])
        wire(ax, [(81.8, cy), (83.5, cy)])
        wire(ax, [(82.3, cy), (82.3, y1 - 3.1), (92.0, y1 - 3.1),
                  (92.0, cy + 0.8)])

        qy = cy + 3.5 if tag == "M" else cy - 3.5
        wire(ax, [(69.0, qy), (73.5, qy)])

    wire(ax, [(69.0, TOP_C), (71.2, TOP_C), (71.2, BOT_C - 0.6),
              (73.5, BOT_C - 0.6)])
    wire(ax, [(69.0, TOP_C - 3.5), (71.2, TOP_C - 3.5)])
    wire(ax, [(69.0, BOT_C), (72.6, BOT_C), (72.6, TOP_C + 0.6),
              (73.5, TOP_C + 0.6)])
    wire(ax, [(69.0, BOT_C + 3.5), (72.6, BOT_C + 3.5)])


# ================================================================ 自检
def verify(fig, name):
    import warnings
    with warnings.catch_warnings(record=True) as wl:
        warnings.simplefilter("always")
        fig.canvas.draw()
        missing = [str(w.message) for w in wl
                   if "missing from font" in str(w.message)
                   and "weight bold" not in str(w.message)]
    r = fig.canvas.get_renderer()
    ax = fig.axes[0]
    fw, fh = fig.canvas.get_width_height()
    prob = []
    if missing:
        prob.append(f"{len(missing)} 处字形缺失：{missing[:2]}")
    boxes = []
    for t in ax.texts:
        tb = t.get_window_extent(renderer=r)
        boxes.append((t, (tb.x0, tb.y0, tb.x1, tb.y1), tb.width * tb.height))
        if tb.x0 < -1 or tb.x1 > fw + 1 or tb.y0 < -1 or tb.y1 > fh + 1:
            prob.append(f"越出画布：「{t.get_text()[:16]}」")
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            a, b = boxes[i][1], boxes[j][1]
            ox = min(a[2], b[2]) - max(a[0], b[0])
            oy = min(a[3], b[3]) - max(a[1], b[1])
            if ox > 0 and oy > 0 and ox * oy > 0.10 * min(boxes[i][2],
                                                           boxes[j][2]):
                prob.append(f"文字互撞：「{boxes[i][0].get_text()[:12]}」×"
                            f"「{boxes[j][0].get_text()[:12]}」")
    print(f"  [自检] {name}：" + ("通过（字形齐全 / 无越界 / 无互撞）"
                                if not prob else "；".join(prob)))
    return prob


# ================================================================ 主流程
def load_face(name):
    p = os.path.join(ASSETS, name)
    if not os.path.isfile(p):
        raise SystemExit(
            f"缺少真实切片文件 {p}\n请先运行： python scripts/extract_case_slices.py")
    return np.asarray(Image.open(p).convert("L"))


def main():
    mri_img = load_face("mri_face.png")
    pet_img = load_face("pet_face.png")

    fig, ax = new_fig(170, 125)
    ax.add_patch(Rectangle((0, 0), 100, 100, facecolor="white",
                           edgecolor="none", zorder=-1))
    ax.add_patch(Rectangle((0.5, 54.5), 99.0, 45.0, facecolor=BG_A,
                           edgecolor="none", zorder=0))
    panel_b(ax)
    panel_c(ax)
    panel_a(ax, mri_img, pet_img)
    panel_a_regions(ax)

    # imshow 会改写坐标范围与纵横比，最后统一复位
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 100)
    ax.set_aspect("auto")

    os.makedirs(OUT, exist_ok=True)
    verify(fig, "fig1_transmf_paper")
    for ext, kw in (("png", {"dpi": DPI}), ("pdf", {}), ("svg", {})):
        p = os.path.join(OUT, f"fig1_transmf_paper.{ext}")
        fig.savefig(p, facecolor="white", **kw)
        print("  [%s] %s" % (ext.upper(), os.path.relpath(p, ROOT)))
    plt.close(fig)


if __name__ == "__main__":
    main()
