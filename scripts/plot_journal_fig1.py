# -*- coding: utf-8 -*-
"""
按《中文核心论文通用模板 · 06_中文核心图表公式细则》重绘 TransMF 模型结构图
（参照示例图 9「卷积神经网络结构示意图」的画法）

风格约束：
  · 灰度 / 细线条 / 简洁线框 / 统一字体 / 少量图例
  · 中文宋体（SimSun），英文与数字 Times New Roman
  · 图内只保留模块名、数据流方向、关键参数，不写长句
  · 图题置于图下方

输出（docs/figures/journal/）：
  fig1_transmf_architecture.png   600 dpi 位图
  fig1_transmf_architecture.pdf   矢量（投稿排版推荐）
  fig1_transmf_architecture.svg   矢量（可再编辑）

用法： python scripts/plot_journal_fig1.py
"""

from __future__ import annotations

import os
import warnings

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, Polygon, Rectangle

# ---------------------------------------------------------------- 字体（宋体 + Times New Roman）
# 注意：字形回退要求把字体列表直接挂在 font.family 上
# （仅设 font.serif 列表时 matplotlib 不会逐字回退，中文会缺字形）
plt.rcParams["font.family"] = ["Times New Roman", "SimSun"]
plt.rcParams["font.serif"] = ["Times New Roman", "SimSun"]
plt.rcParams["axes.unicode_minus"] = False
plt.rcParams["pdf.fonttype"] = 42
plt.rcParams["ps.fonttype"] = 42
plt.rcParams["hatch.linewidth"] = 0.5

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "docs", "figures", "journal")

# ---------------------------------------------------------------- 灰度配色
G_MRI = "#FFFFFF"     # MRI 体数据
G_PET = "#D0D0D0"     # PET 体数据
G_CONV = "#F2F2F2"    # 3D 卷积块
G_ATTN = "#BFBFBF"    # 跨模态注意力层
G_POOL = "#E6E6E6"    # 池化层
G_FC = "#A6A6A6"      # 全连接层
G_OUT = "#808080"     # 输出层
G_GRL = "#EDEDED"     # 梯度反转层（叠加斜线）
G_BOX = "#FFFFFF"     # 分组框底色
EC = "#000000"
GROUP_EC = "#404040"

LW_THIN = 0.5
LW_BOX = 0.7
LW_ARROW = 0.7
MS = 5.0

FS_TITLE = 8.0
FS_NOTE = 6.0
FS_GROUP = 6.8
FS_LABEL = 6.0
FS_LEGEND = 6.0

_GROUPS = []      # 所有分组框矩形，用于几何自检
_SLABS = []       # 所有切片叠层的外接框，用于「文字压切片」自检
ON_PLATE = {"MRI", "PET"}   # 有意画在切片表面上的标签
FOOTER_TOP = 30.0  # 图例 / 图题 / 注 所在区域上界


def new_fig(w_in, h_in, ylo=0.0):
    """ylo>0 时裁掉画布底部（用于输出不带图题的版本，内容比例保持一致）"""
    _GROUPS.clear()
    _SLABS.clear()
    fig = plt.figure(figsize=(w_in, h_in))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 100)
    ax.set_ylim(ylo, 100)
    ax.axis("off")
    return fig, ax


def _shift(hexcolor, f):
    """f>0 变浅，f<0 变深（灰度）"""
    h = hexcolor.lstrip("#")
    rgb = [int(h[i:i + 2], 16) for i in (0, 2, 4)]
    if f >= 0:
        rgb = [int(c + (255 - c) * f) for c in rgb]
    else:
        rgb = [int(c * (1 + f)) for c in rgb]
    return "#%02X%02X%02X" % tuple(max(0, min(255, c)) for c in rgb)


def plate(ax, cx, cy, w, h, dx=0.7, dy=0.7, fc="#F2F2F2", ec=EC, lw=LW_THIN,
          hatch=None, z=4):
    """单块 3D 立体切片（正面 + 顶面 + 侧面）"""
    x0, y0 = cx - w / 2.0, cy - h / 2.0
    ax.add_patch(Polygon(
        [(x0, y0 + h), (x0 + dx, y0 + h + dy), (x0 + w + dx, y0 + h + dy),
         (x0 + w, y0 + h)], closed=True, facecolor=_shift(fc, 0.55),
        edgecolor=ec, linewidth=lw, zorder=z))
    ax.add_patch(Polygon(
        [(x0 + w, y0), (x0 + w + dx, y0 + dy), (x0 + w + dx, y0 + h + dy),
         (x0 + w, y0 + h)], closed=True, facecolor=_shift(fc, -0.18),
        edgecolor=ec, linewidth=lw, zorder=z))
    ax.add_patch(Rectangle(
        (x0, y0), w, h, facecolor=fc, edgecolor=ec, linewidth=lw,
        hatch=hatch, zorder=z + 0.1))


def stack(ax, cx, cy, w, h, n=3, dx=0.7, dy=0.7, ds=0.55, fc="#F2F2F2",
          ec=EC, lw=LW_THIN, hatch=None, z=4):
    """叠层切片；(cx, cy) 为整叠外接框的中心"""
    ox = ((n - 1) * ds + dx) / 2.0
    oy = ((n - 1) * ds + dy) / 2.0
    _SLABS.append((cx - ox - w / 2, cy - oy - h / 2,
                   cx + ox + w / 2, cy + oy + h / 2))
    for i in range(n - 1, -1, -1):
        plate(ax, cx - ox + i * ds, cy - oy + i * ds, w, h, dx, dy, fc, ec,
              lw, hatch, z + (n - 1 - i) * 0.01)


def group(ax, x0, y0, x1, y1, title=None, fc=G_BOX, z=1, dashed=True,
          ts=FS_GROUP):
    """分组框（默认虚线）+ 上方标题"""
    ax.add_patch(FancyBboxPatch(
        (x0, y0), x1 - x0, y1 - y0,
        boxstyle="round,pad=0,rounding_size=0.5", linewidth=LW_BOX,
        edgecolor=GROUP_EC, facecolor=fc, zorder=z,
        linestyle=(0, (2.5, 1.6)) if dashed else "-"))
    _GROUPS.append((x0, y0, x1, y1 + 4.0 if title else y1))
    if title:
        ax.text((x0 + x1) / 2.0, y1 + 1.5, title, ha="center", va="bottom",
                fontsize=ts, color=EC, zorder=z + 5)


def txt(ax, x, y, s, size=FS_LABEL, ha="center", va="center", rot=0, z=8,
        bold=False):
    return ax.text(x, y, s, fontsize=size, ha=ha, va=va, rotation=rot,
                   color=EC, zorder=z, fontweight="bold" if bold else "normal")


def arrow(ax, p0, p1, color=EC, lw=LW_ARROW, ms=MS, style="-|>", z=6, ls="-"):
    ax.add_patch(FancyArrowPatch(
        p0, p1, arrowstyle=style, mutation_scale=ms, linewidth=lw,
        color=color, zorder=z, shrinkA=0, shrinkB=0, linestyle=ls))


def harrow(ax, x0, x1, y, **kw):
    arrow(ax, (x0, y), (x1, y), **kw)


def varrow(ax, x, y0, y1, **kw):
    arrow(ax, (x, y0), (x, y1), **kw)


def dot(ax, x, y, r=0.32, z=9):
    ax.add_patch(plt.Circle((x, y), r, facecolor=EC, edgecolor=EC,
                            linewidth=0.3, zorder=z))


def verify(fig, name):
    with warnings.catch_warnings(record=True) as wlist:
        warnings.simplefilter("always")
        fig.canvas.draw()
        glyph = [str(w.message) for w in wlist
                 if "missing from font" in str(w.message)
                 and "weight bold" not in str(w.message)]
    r = fig.canvas.get_renderer()
    ax = fig.axes[0]
    prob = []
    if glyph:
        prob.append(f"{len(glyph)} 处字形缺失：{glyph[:2]}")

    fw, fh = fig.canvas.get_width_height()
    texts = [t for t in ax.texts]
    boxes = []
    for t in texts:
        tb = t.get_window_extent(renderer=r)
        if tb.x0 < -1 or tb.x1 > fw + 1 or tb.y0 < -1 or tb.y1 > fh + 1:
            prob.append(f"越出画布：「{t.get_text()[:16]}」")
        boxes.append((t, tb))

    # ① 每段文字必须落在某个分组框（含标题带）或底部图例/图题区内
    regions = [(x0, y0, x1, y1) for x0, y0, x1, y1 in _GROUPS]
    regions.append((0.0, 0.0, 100.0, FOOTER_TOP))
    disp = []
    for x0, y0, x1, y1 in regions:
        (dx0, dy0) = ax.transData.transform((x0, y0))
        (dx1, dy1) = ax.transData.transform((x1, y1))
        disp.append((min(dx0, dx1), min(dy0, dy1), max(dx0, dx1), max(dy0, dy1)))
    for t, tb in boxes:
        if not any(dx0 - 1 <= tb.x0 and tb.x1 <= dx1 + 1
                   and dy0 - 1 <= tb.y0 and tb.y1 <= dy1 + 1
                   for dx0, dy0, dx1, dy1 in disp):
            prob.append(f"越出分组框：「{t.get_text()[:16]}」")

    # ② 文字互撞
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            a, b = boxes[i][1], boxes[j][1]
            ox = min(a.x1, b.x1) - max(a.x0, b.x0)
            oy = min(a.y1, b.y1) - max(a.y0, b.y0)
            if ox > 0 and oy > 0 and ox * oy > 0.10 * min(
                    a.width * a.height, b.width * b.height):
                prob.append(
                    f"文字互撞：「{boxes[i][0].get_text()[:12]}」×"
                    f"「{boxes[j][0].get_text()[:12]}」")

    # ③ 文字压切片（MRI / PET 为有意画在切片表面，其余均属缺陷）
    slabs = []
    for x0, y0, x1, y1 in _SLABS:
        (dx0, dy0) = ax.transData.transform((x0, y0))
        (dx1, dy1) = ax.transData.transform((x1, y1))
        slabs.append((min(dx0, dx1), min(dy0, dy1), max(dx0, dx1), max(dy0, dy1)))
    for t, tb in boxes:
        if t.get_text() in ON_PLATE:
            continue
        for sx0, sy0, sx1, sy1 in slabs:
            ox = min(tb.x1, sx1) - max(tb.x0, sx0)
            oy = min(tb.y1, sy1) - max(tb.y0, sy0)
            if ox > 0 and oy > 0 and ox * oy > 0.05 * tb.width * tb.height:
                prob.append(f"文字压切片：「{t.get_text()[:16]}」")
                break

    print(f"  [自检] {name}：" + ("通过（字形齐全 / 无越界 / 无互撞 / 无压切片）"
                                if not prob else "；".join(prob)))
    return prob


def save(fig, stem):
    os.makedirs(OUT, exist_ok=True)
    verify(fig, stem)
    for ext, kw in (("png", {"dpi": 600}), ("pdf", {}), ("svg", {})):
        p = os.path.join(OUT, f"{stem}.{ext}")
        fig.savefig(p, facecolor="white", **kw)
        print(f"  [{ext.upper()}] {os.path.relpath(p, ROOT)}")
    plt.close(fig)


# ================================================================ 版面常量
Y0, Y1 = 61.0, 94.0            # 主流程分组框上下边界
Y_TITLE = 95.5                 # 分组标题基线
Y_MRI, Y_PET = 84.0, 72.0      # MRI / PET 支路中心
Y_C = 78.0                     # 融合后主干中心

X_IN0, X_IN1 = 1.5, 9.5
X_ENC0, X_ENC1 = 11.0, 49.0
X_FU0, X_FU1 = 51.5, 70.0
X_PO0, X_PO1 = 72.0, 78.5
X_FC0, X_FC1 = 80.5, 88.5
X_OU0, X_OU1 = 90.5, 98.5
X_CORR = 50.0                  # 对抗支路取线通道

Y_ADV0, Y_ADV1 = 28.0, 48.0    # 对抗支路外层分组框
Y_ADV_C = 37.75                # 对抗支路各子框中心
AY0, AY1 = 31.5, 44.0          # 对抗支路各子框上下边界
Y_RUN = 53.0                   # 取线横向走线高度
X_TAP = 16.0                   # 取线从左侧进入对抗支路


def draw_main(ax):
    # ---------- 输入层 ----------
    group(ax, X_IN0, Y0, X_IN1, Y1, "输入层")
    cx = (X_IN0 + X_IN1) / 2
    stack(ax, cx, Y_MRI, 5.4, 6.4, n=3, ds=0.5, fc=G_MRI)
    stack(ax, cx, Y_PET, 5.4, 6.4, n=3, ds=0.5, fc=G_PET)
    txt(ax, cx, Y_MRI, "MRI", z=9)
    txt(ax, cx, Y_PET, "PET", z=9)
    txt(ax, cx, Y_C, "91×109×91")

    # ---------- 模态编码器 sNet ×2 ----------
    group(ax, X_ENC0, Y0, X_ENC1, Y1, "模态编码器 sNet ×2（双支路，参数独立）")
    bx0, bw, bgap = X_ENC0 + 0.8, 8.7, 0.6
    specs = [("卷积块 1", "C=32", "45×54×45"),
             ("卷积块 2", "C=64", "22×27×22"),
             ("卷积块 3", "C=128", "11×13×11"),
             ("卷积块 4", "C=128", "5×6×5")]
    for i, (t, c, sp) in enumerate(specs):
        x0 = bx0 + i * (bw + bgap)
        cx = x0 + bw / 2
        group(ax, x0, Y0 + 2.0, x0 + bw, Y0 + 29.5, t)
        txt(ax, cx, Y0 + 5.5, c)
        txt(ax, cx, Y0 + 3.2, sp)
        stack(ax, cx, Y_MRI, 6.4, 6.0, n=3, ds=0.5, fc=G_CONV)
        stack(ax, cx, Y_PET, 6.4, 6.0, n=3, ds=0.5, fc=G_CONV)

    # ---------- 跨模态交叉注意力融合层 ----------
    group(ax, X_FU0, Y0, X_FU1, Y1, "跨模态交叉注意力融合层")
    cx = (X_FU0 + X_FU1) / 2
    txt(ax, cx, Y1 - 2.2, "Token 化")
    stack(ax, cx, Y_C, 9.5, 16.0, n=3, ds=0.8, dx=1.0, dy=1.0, fc=G_ATTN)
    txt(ax, cx, 65.5, "交叉注意力 ×3")

    # ---------- 池化层 ----------
    group(ax, X_PO0, Y0, X_PO1, Y1, "池化层")
    cx = (X_PO0 + X_PO1) / 2
    stack(ax, cx, Y_C, 3.4, 18.0, n=2, ds=0.5, fc=G_POOL)
    txt(ax, cx, 65.5, "GAP+GMP")
    txt(ax, cx, 62.8, "512-d")

    # ---------- 全连接层 ----------
    group(ax, X_FC0, Y0, X_FC1, Y1, "全连接层")
    cx = (X_FC0 + X_FC1) / 2
    stack(ax, cx, Y_C, 4.2, 18.0, n=3, ds=0.5, fc=G_FC)
    txt(ax, cx, 65.5, "512→512")
    txt(ax, cx, 62.8, "→64→2")

    # ---------- 输出层 ----------
    group(ax, X_OU0, Y0, X_OU1, Y1, "输出层")
    cx = (X_OU0 + X_OU1) / 2
    stack(ax, cx, Y_C, 3.6, 15.0, n=2, ds=0.5, fc=G_OUT)
    txt(ax, cx, 65.5, "Softmax")
    txt(ax, cx, 62.8, "CN / AD")

    # ---------- 主干数据流 ----------
    harrow(ax, X_IN1, X_ENC0, Y_MRI)
    harrow(ax, X_IN1, X_ENC0, Y_PET)
    harrow(ax, X_ENC1, X_FU0, Y_MRI)
    harrow(ax, X_ENC1, X_FU0, Y_PET)
    harrow(ax, X_FU1, X_PO0, Y_C)
    harrow(ax, X_PO1, X_FC0, Y_C)
    harrow(ax, X_FC1, X_OU0, Y_C)

    # ---------- 对抗支路取线 ----------
    dot(ax, X_CORR, Y_MRI)
    dot(ax, X_CORR, Y_PET)
    y_run = Y_RUN
    arrow(ax, (X_CORR, Y_MRI), (X_CORR, y_run), style="-")
    arrow(ax, (X_CORR, y_run), (X_TAP, y_run), style="-")
    arrow(ax, (X_TAP, y_run), (X_TAP, Y_ADV_C), style="-")
    harrow(ax, X_TAP, 20.0, Y_ADV_C)


def draw_adv(ax):
    group(ax, 18, Y_ADV0, 74, Y_ADV1, "对抗域判别支路")
    items = [("全局平均池化", ["AdaptiveAvgPool3d(1)"], 20, 33, G_CONV, None),
             ("梯度反转层", ["revgrad(x, α=2)"], 35, 45, G_GRL, "////"),
             ("域判别器 D", ["Linear 128→128", "Linear 128→2"], 47, 60, G_CONV, None),
             ("域判别输出", ["MRI 域 = 1", "PET 域 = 0"], 62, 72, G_CONV, None)]
    for t, ls, x0, x1, fc, hatch in items:
        group(ax, x0, AY0, x1, AY1, t, fc=G_BOX)
        stack(ax, (x0 + x1) / 2, 35.0, 5.0, 3.0, n=2, ds=0.45, fc=fc,
              hatch=hatch)
        txt(ax, (x0 + x1) / 2, 41.3, "\n".join(ls))
    for x0, x1 in ((33, 35), (45, 47), (60, 62)):
        harrow(ax, x0, x1, Y_ADV_C)


def draw_footer(ax, with_caption=True):
    rows = [("体素数据 MRI", G_MRI, None),
            ("体素数据 PET", G_PET, None),
            ("3D 卷积块", G_CONV, None),
            ("跨模态注意力层", G_ATTN, None),
            ("池化层", G_POOL, None),
            ("全连接层", G_FC, None),
            ("输出层", G_OUT, None),
            ("梯度反转层", G_GRL, "////")]
    for k, y in enumerate((22.0, 15.0)):
        for j, (label, fc, hatch) in enumerate(rows[k * 4:(k + 1) * 4]):
            x = 21.0 + j * 15.5
            ax.add_patch(Rectangle((x, y - 1.05), 2.1, 2.1, facecolor=fc,
                                   edgecolor=EC, linewidth=LW_THIN,
                                   hatch=hatch, zorder=6))
            txt(ax, x + 2.8, y, label, size=FS_LEGEND, ha="left")
    if not with_caption:
        return
    ax.text(50, 10.2, "图 1　TransMF 多模态融合网络结构与数据流",
            fontsize=FS_TITLE, ha="center", va="center", color=EC, zorder=8,
            fontproperties=FontProperties(
                family=["SimHei", "Times New Roman"]))
    txt(ax, 50, 6.2,
        "注：MRI 与 PET 支路结构相同、参数独立；卷积块由 Conv3d + "
        "InstanceNorm3d + LeakyReLU 组成。",
        size=FS_NOTE)
    txt(ax, 50, 3.2,
        "　　各卷积块空间下采样 2 倍（前 3 块 MaxPool，第 4 块 AvgPool，"
        "末端 1×1×1 卷积压缩通道）；梯度反转层反向梯度取反。",
        size=FS_NOTE)


def main():
    # 170 mm 通栏；两版内容比例完全一致，仅差底部图题带
    for stem, ylo, h_in, cap in (
            ("fig1_transmf_architecture", 0.0, 4.72, True),
            ("fig1_transmf_architecture_nocaption", 13.0, 4.107, False)):
        fig, ax = new_fig(6.69, h_in, ylo=ylo)
        draw_main(ax)
        draw_adv(ax)
        draw_footer(ax, with_caption=cap)
        save(fig, stem)


if __name__ == "__main__":
    print("生成中文核心期刊风格图 →", os.path.relpath(OUT, ROOT))
    main()
    print("完成。")
