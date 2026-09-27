# -*- coding: utf-8 -*-
"""
生成 ADScreen / TransMF 项目的网络结构图（高清 PNG + Mermaid 源码）

输出目录：docs/figures/
  01_model_architecture.png / .mmd   TransMF 模型网络结构（models/mymodel.py -> model_ad）
  02_ensemble_pipeline.png  / .mmd   快照集成推理链路（ad-screen-backend/services/model_inference.py）
  03_system_architecture.png/ .mmd   平台整体系统架构
  04_training_pipeline.png  / .mmd   K 折对抗训练流程

用法：
    python scripts/plot_network_diagrams.py
"""

from __future__ import annotations

import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["font.family"] = "sans-serif"
plt.rcParams["axes.unicode_minus"] = False

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "docs", "figures")
DPI = 300

# ---------------------------------------------------------------- 调色板
MRI_FC, MRI_EC = "#DCE9F7", "#1D4E89"
PET_FC, PET_EC = "#FDE7D3", "#B35C00"
FUSE_FC, FUSE_EC = "#EAE3F5", "#5B3E8E"
HEAD_FC, HEAD_EC = "#D6EFE6", "#0F6B57"
ADV_FC, ADV_EC = "#FBDCD6", "#A93226"
IN_FC, IN_EC = "#ECEFF4", "#4A5568"
OUT_FC, OUT_EC = "#E2E8F0", "#1F2937"
LOSS_FC, LOSS_EC = "#FFF7DC", "#9A7B0A"
NOTE_FC, NOTE_EC = "#F5F8FC", "#94A3B8"
BAND_FC, BAND_EC = "#F8FAFC", "#CBD5E1"
EDGE, TXT, SUB = "#475569", "#1E293B", "#64748B"

_UPT = 0.126  # 1pt 在 y 轴上的数据单位（由 new_fig 按图高重算）
_REG = []     # 排版自检登记表：(ax, patch, [text artist], 描述)


# ---------------------------------------------------------------- 基础绘图工具
def new_fig(w, h):
    global _UPT
    _UPT = 100.0 / (72.0 * h)
    _REG.clear()
    fig = plt.figure(figsize=(w, h))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 100)
    ax.axis("off")
    return fig, ax


def box(ax, x, y, w, h, title=None, lines=(), fc="#FFFFFF", ec=EDGE, lw=1.5,
        ts=10.0, bs=8.0, tc=None, bc=TXT, rounding=0.7, z=3, pad=1.0,
        linespacing=1.5, linestyle="-", alpha=1.0):
    """圆角矩形 + 标题 + 正文（多行居中）"""
    patch = FancyBboxPatch(
        (x, y), w, h, boxstyle=f"round,pad=0,rounding_size={rounding}",
        linewidth=lw, edgecolor=ec, facecolor=fc, zorder=z,
        linestyle=linestyle, alpha=alpha)
    ax.add_patch(patch)
    artists = []
    cx = x + w / 2.0
    body_top = y + h - pad
    if title:
        artists.append(ax.text(
            cx, y + h - pad, title, ha="center", va="top", fontsize=ts,
            fontweight="bold", color=tc or ec, zorder=z + 2))
        body_top -= ts * _UPT * 1.7
    if lines:
        artists.append(ax.text(
            cx, y + pad + max(body_top - (y + pad), 0.1) / 2.0, "\n".join(lines),
            ha="center", va="center", fontsize=bs, color=bc, zorder=z + 2,
            linespacing=linespacing))
    _REG.append((ax, patch, artists, (title or (lines[0] if lines else ""))[:24]))


def verify(fig, name, tol_px=1.5):
    """渲染后校验：每段文字必须完整落在所属方框内（防止排版溢出）"""
    fig.canvas.draw()
    r = fig.canvas.get_renderer()
    bad = []
    for ax, patch, artists, desc in _REG:
        pb = patch.get_window_extent(renderer=r)
        for t in artists:
            tb = t.get_window_extent(renderer=r)
            if (tb.x0 < pb.x0 - tol_px or tb.x1 > pb.x1 + tol_px
                    or tb.y0 < pb.y0 - tol_px or tb.y1 > pb.y1 + tol_px):
                bad.append((desc, round(tb.x1 - pb.x1, 1), round(pb.x0 - tb.x0, 1),
                            round(tb.y1 - pb.y1, 1), round(pb.y0 - tb.y0, 1)))
    if bad:
        print(f"  [溢出告警] {name}：{len(bad)} 处文字超出方框")
        for d, rx, lx, ry, ly in bad:
            print(f"      - 「{d}」 右溢{rx:>7} 左溢{lx:>7} 上溢{ry:>7} 下溢{ly:>7} (px)")
    else:
        print(f"  [排版自检] {name}：全部文字位于方框内")

    # 整图边界检查：任何文字都不得越出画布
    fw, fh = fig.canvas.get_width_height()
    outs = []
    for ax in fig.axes:
        for t in ax.texts:
            tb = t.get_window_extent(renderer=r)
            if tb.x0 < -1 or tb.x1 > fw + 1 or tb.y0 < -1 or tb.y1 > fh + 1:
                outs.append((t.get_text()[:26], round(tb.x0, 1), round(tb.x1, 1),
                             round(tb.y0, 1), round(tb.y1, 1)))
    if outs:
        print(f"  [越界告警] {name}：{len(outs)} 处文字越出画布（画布 {fw}×{fh} px）")
        for d, x0, x1, y0, y1 in outs:
            print(f"      - 「{d}」 x∈[{x0},{x1}] y∈[{y0},{y1}]")
    else:
        print(f"  [边界自检] {name}：全部文字位于画布内")
    # 文字互撞 / 文字压箭头检测
    def _inter(a, b):
        dx = min(a.x1, b.x1) - max(a.x0, b.x0)
        dy = min(a.y1, b.y1) - max(a.y0, b.y0)
        return dx * dy if dx > 0 and dy > 0 else 0.0

    texts = [(t, t.get_window_extent(renderer=r)) for t in ax.texts]
    clashes = []
    for i in range(len(texts)):
        for j in range(i + 1, len(texts)):
            (t1, b1), (t2, b2) = texts[i], texts[j]
            ov = _inter(b1, b2)
            if ov > 0.12 * min(b1.width * b1.height, b2.width * b2.height):
                clashes.append(("文字×文字", t1.get_text()[:18], t2.get_text()[:18]))
    for t, tb in texts:
        ta = tb.width * tb.height
        if ta <= 0:
            continue
        for p in ax.patches:
            if not isinstance(p, FancyArrowPatch):
                continue
            pb = p.get_window_extent(renderer=r)
            if _inter(tb, pb) > 0.30 * ta:
                clashes.append(("文字×箭头", t.get_text()[:18], ""))
    if clashes:
        print(f"  [碰撞告警] {name}：{len(clashes)} 处")
        for kind, a, b in clashes[:15]:
            print(f"      - {kind}：「{a}」 ×「{b}」")
    else:
        print(f"  [碰撞自检] {name}：无文字互撞 / 无文字压箭头")

    # 拥挤度诊断：文字渲染尺寸 / 方框尺寸，>0.88 说明过于贴边
    tight = []
    for ax, patch, artists, desc in _REG:
        if not artists:
            continue
        pb = patch.get_window_extent(renderer=r)
        bw, bh = pb.x1 - pb.x0, pb.y1 - pb.y0
        tw = max(t.get_window_extent(renderer=r).x1 -
                 t.get_window_extent(renderer=r).x0 for t in artists)
        th = max(t.get_window_extent(renderer=r).y1 -
                 t.get_window_extent(renderer=r).y0 for t in artists)
        tight.append((max(tw / bw, th / bh), desc, round(tw / bw, 2),
                      round(th / bh, 2), round(bh / fig.dpi, 3)))
    tight.sort(reverse=True)
    hot = [t for t in tight if t[0] > 0.88]
    print(f"  [填充率] {name}：最挤 5 处 " + "，".join(
        f"{d}={r:.2f}" for r, d, _, _, _ in tight[:5]))
    if hot:
        print(f"  [拥挤提示] {name}：{len(hot)} 处方框填充率 > 0.88")
        for ratio, desc, fw_, fh_, bh_in in hot[:12]:
            print(f"      - 「{desc}」 宽填充{fw_} 高填充{fh_}（框高{bh_in}in）")

    return bad + outs + [1] * len(clashes)


def arrow(ax, p0, p1, color=EDGE, lw=1.6, ms=13, rad=0.0, ls="-", z=2,
          style="-|>"):
    ax.add_patch(FancyArrowPatch(
        p0, p1, arrowstyle=style, mutation_scale=ms, linewidth=lw, color=color,
        linestyle=ls, zorder=z, shrinkA=0, shrinkB=0,
        connectionstyle=f"arc3,rad={rad}"))


def harrow(ax, x0, x1, y, **kw):
    arrow(ax, (x0, y), (x1, y), **kw)


def varrow(ax, x, y0, y1, **kw):
    arrow(ax, (x, y0), (x, y1), **kw)


def label(ax, x, y, text, size=8.0, color=SUB, ha="center", va="center",
          rot=0, bold=False, z=5):
    ax.text(x, y, text, fontsize=size, color=color, ha=ha, va=va, rotation=rot,
            fontweight="bold" if bold else "normal", zorder=z)


def heading(ax, main, sub=None):
    label(ax, 50, 99.0, main, size=19, color="#0F172A", va="top", bold=True)
    if sub:
        label(ax, 50, 96.4, sub, size=10.5, color=SUB, va="top")


def band(ax, x, y, w, h, title, fc=BAND_FC, ec=BAND_EC, z=1):
    """分层背景带 + 左侧层名标签"""
    ax.add_patch(FancyBboxPatch(
        (x, y), w, h, boxstyle="round,pad=0,rounding_size=0.8",
        linewidth=1.3, edgecolor=ec, facecolor=fc, zorder=z))
    box(ax, x + 0.9, y + 0.9, 11.5, h - 1.8, None, title.split("\n"),
        fc="#FFFFFF", ec=ec, ts=10.0, bs=10.0, tc="#0F172A", bc="#334155",
        lw=1.2, z=z + 1, linespacing=1.5)


def save(fig, name):
    os.makedirs(OUT, exist_ok=True)
    verify(fig, name)
    path = os.path.join(OUT, name)
    fig.savefig(path, dpi=DPI, facecolor="white")
    plt.close(fig)
    print(f"  [PNG] {os.path.relpath(path, ROOT)}")


def save_text(name, text):
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, name)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text.strip() + "\n")
    print(f"  [MMD] {os.path.relpath(path, ROOT)}")


# ================================================================ 图 1：模型网络结构
def fig_model_architecture():
    fig, ax = new_fig(20, 11)
    heading(ax,
            "TransMF 多模态融合网络结构（models/mymodel.py → model_ad）",
            "MRI + PET 双分支 3D-CNN 编码 → 跨模态 Transformer 注意力融合 → 分类头；"
            "并行梯度反转对抗分支，学习 MRI / PET 模态不可分的共享表征")

    # ---------- 主分支：输入 ----------
    box(ax, 1.5, 75, 11, 14, "MRI 输入",
        ["T1 结构像", "NIfTI 1×X×Y×Z", "强度归一化"], fc=IN_FC, ec=MRI_EC, ts=9.5, bs=7.6)
    box(ax, 1.5, 58, 11, 14, "PET 输入",
        ["FDG 代谢像", "NIfTI 1×X×Y×Z", "强度归一化"], fc=IN_FC, ec=PET_EC, ts=9.5, bs=7.6)

    # ---------- 主分支：编码器 ----------
    box(ax, 15, 75, 15.5, 14, "sNet 3D-CNN",
        ["Conv3d 1→32→64→128→128", "InstanceNorm3d + LeakyReLU", "MaxPool ×3 + AvgPool ×1"],
        fc=MRI_FC, ec=MRI_EC, ts=9.5, bs=7.5)
    box(ax, 15, 58, 15.5, 14, "sNet 3D-CNN",
        ["Conv3d 1→32→64→128→128", "InstanceNorm3d + LeakyReLU", "MaxPool ×3 + AvgPool ×1"],
        fc=PET_FC, ec=PET_EC, ts=9.5, bs=7.5)

    # ---------- 主分支：token 化 ----------
    box(ax, 32.5, 76.5, 8, 11, "Flatten",
        ["b·d·x·y·z", "→ b·(xyz)·d", "150 × 128"], fc=MRI_FC, ec=MRI_EC, ts=9, bs=7.2)
    box(ax, 32.5, 59.5, 8, 11, "Flatten",
        ["b·d·x·y·z", "→ b·(xyz)·d", "150 × 128"], fc=PET_FC, ec=PET_EC, ts=9, bs=7.2)

    # ---------- 融合模块 ----------
    box(ax, 43, 42, 27, 50, "CrossTransformer_MOD_AVG（depth = 3）", (),
        fc="#FAF8FE", ec=FUSE_EC, lw=1.8, ts=10.5)
    layer_lines = [
        "mri ← CrossAttn(Q=mri, KV=pet) + mri",
        "pet ← CrossAttn(Q=pet, KV=mri) + pet",
        "PreNorm · MHSA(4 heads, d_head=32) · FFN(512) · LN",
    ]
    for i, yy in enumerate((77.0, 66.5, 56.0), start=1):
        box(ax, 44.5, yy, 24, 8.5, f"Layer {i}", layer_lines,
            fc=FUSE_FC, ec=FUSE_EC, ts=9, bs=7.1, lw=1.3)
    varrow(ax, 56.5, 75.0, 77.0, lw=1.3)
    varrow(ax, 56.5, 64.5, 66.5, lw=1.3)
    box(ax, 44.5, 44, 24, 10, "GAP + GMP 池化",
        ["mri / pet 各做全局平均 + 全局最大池化",
         "concat → 4·dim = 512-d cls token"],
        fc="#F3EDFB", ec=FUSE_EC, ts=9, bs=7.1, lw=1.3)
    varrow(ax, 56.5, 56.0, 54.0, lw=1.3)

    # ---------- 注意力说明面板 ----------
    box(ax, 73.5, 64, 26, 26, "跨模态注意力机制",
        ["Attention(Q, K, V) = softmax(QK^T / sqrt(d_head)) · V",
         "Q 取自本模态 token，K / V 取自另一模态 token",
         "两层交叉注意力互为 Q 与 KV，逐层显式对齐",
         "建模 MRI 形态学与 PET 代谢的空间对应关系",
         "每个 block：PreNorm → MHSA → 残差 → FFN → 残差 → LN"],
        fc=NOTE_FC, ec=NOTE_EC, ts=10, bs=7.6)

    # ---------- 分类头与输出 ----------
    box(ax, 73.5, 40, 13.5, 20, "分类头 fc_cls",
        ["Linear 512→512", "LayerNorm+ReLU", "Dropout 0.5", "Linear 512→64",
         "LayerNorm+ReLU", "Linear 64→2 (CN/AD)"],
        fc=HEAD_FC, ec=HEAD_EC, ts=9.5, bs=7.2)
    box(ax, 89.5, 42, 10, 16, "输出",
        ["logits (b×2)", "Softmax", "→ p(AD)", "阈值判定"],
        fc=OUT_FC, ec=OUT_EC, ts=9.5, bs=7.4)

    harrow(ax, 68.5, 73.5, 49.0)
    label(ax, 71.0, 51.5, "512-d", size=7.0)
    harrow(ax, 87.0, 89.5, 50.0)

    # ---------- 主分支连线 ----------
    harrow(ax, 12.5, 15.0, 82.0)
    harrow(ax, 12.5, 15.0, 65.0)
    harrow(ax, 30.5, 32.5, 82.0)
    harrow(ax, 30.5, 32.5, 65.0)
    harrow(ax, 40.5, 43.0, 82.0)
    harrow(ax, 40.5, 43.0, 65.0)

    # ---------- 对抗分支 ----------
    varrow(ax, 25.0, 75.0, 37.0, color=ADV_EC)
    varrow(ax, 19.0, 58.0, 37.0, color=ADV_EC)
    for xx in (25.0, 19.0):
        ax.plot([xx], [75.0 if xx == 25.0 else 58.0], marker="o", ms=5,
                color=ADV_EC, zorder=6)

    box(ax, 13, 24, 16, 13, "GAP 全局平均池化",
        ["AdaptiveAvgPool3d(1)", "特征图 → 128-d 向量"], fc=ADV_FC, ec=ADV_EC,
        ts=9, bs=7.3)
    box(ax, 33, 24, 14, 13, "梯度反转层",
        ["revgrad(x, α = 2)", "前向：恒等", "反向：梯度 ×(−α)"], fc=ADV_FC,
        ec=ADV_EC, ts=9, bs=7.3)
    box(ax, 51, 20, 17, 21, "域判别器 D",
        ["Linear 128→128", "LayerNorm + ReLU", "Linear 128→2",
         "→ D_MRI_logits", "→ D_PET_logits"], fc=ADV_FC, ec=ADV_EC, ts=9.5, bs=7.3)
    box(ax, 72, 24, 15, 13, "域标签（对抗目标）",
        ["MRI 域 y = 1", "PET 域 y = 0"], fc=ADV_FC, ec=ADV_EC, ts=9, bs=7.3)

    harrow(ax, 29.0, 33.0, 30.5, color=ADV_EC)
    harrow(ax, 47.0, 51.0, 30.5, color=ADV_EC)
    harrow(ax, 68.0, 72.0, 30.5, color=ADV_EC)

    label(ax, 37.0, 45.0,
          "对抗分支输入\n= sNet 输出特征图\n（与主分支共享编码器，\n未做 flatten）",
          size=7.2, color=ADV_EC, ha="center", va="center")

    # ---------- 损失聚合 ----------
    box(ax, 60, 3, 39.5, 14, "总损失  total = L_cls + L_adv",
        ["L_cls = LSCE(logits, y)，权重 1.0（label_smooth = 0.05）",
         "L_adv = ad_loss_weight(0.1) × [CE(D_MRI, 1) + CE(D_PET, 0)] / 2",
         "梯度反转使编码器被“惩罚”到无法区分模态 → 学到 MRI/PET 共享表征"],
        fc=LOSS_FC, ec=LOSS_EC, ts=10, bs=7.4)

    varrow(ax, 94.5, 42.0, 17.0)
    varrow(ax, 79.5, 24.0, 17.0)
    label(ax, 96.0, 30.0, "L_cls", size=7.5, color=LOSS_EC, rot=90)
    label(ax, 81.0, 20.5, "L_adv", size=7.5, color=LOSS_EC)

    label(ax, 1.5, 1.2,
          "超参（options/option.py 默认值，与后端 MODEL_* 常量一致）："
          "dim=128，depth=3，heads=4，dim_head=32，mlp_dim=512，dropout=0.15，num_classes=2；"
          "MRI 与 PET 分支结构相同、参数各自独立。",
          size=7.8, color=SUB, ha="left", va="bottom")

    save(fig, "01_model_architecture.png")


MERMAID_01 = """
%% TransMF 模型网络结构（models/mymodel.py -> model_ad）
flowchart LR
    classDef mri fill:#DCE9F7,stroke:#1D4E89,stroke-width:1.5px,color:#0F172A
    classDef pet fill:#FDE7D3,stroke:#B35C00,stroke-width:1.5px,color:#0F172A
    classDef fuse fill:#EAE3F5,stroke:#5B3E8E,stroke-width:1.5px,color:#0F172A
    classDef head fill:#D6EFE6,stroke:#0F6B57,stroke-width:1.5px,color:#0F172A
    classDef adv fill:#FBDCD6,stroke:#A93226,stroke-width:1.5px,color:#0F172A
    classDef io fill:#ECEFF4,stroke:#4A5568,stroke-width:1.5px,color:#0F172A
    classDef loss fill:#FFF7DC,stroke:#9A7B0A,stroke-width:1.5px,color:#0F172A

    subgraph IN["输入"]
        direction TB
        MRI["MRI T1 结构像<br/>1 × X × Y × Z"]:::io
        PET["PET FDG 代谢像<br/>1 × X × Y × Z"]:::io
    end

    subgraph ENC["模态编码器 sNet（3D-CNN）"]
        direction TB
        ENC_M["sNet(MRI)<br/>Conv3d 1→32→64→128→128<br/>InstanceNorm3d + LeakyReLU<br/>MaxPool×3 + AvgPool×1"]:::mri
        ENC_P["sNet(PET)<br/>Conv3d 1→32→64→128→128<br/>InstanceNorm3d + LeakyReLU<br/>MaxPool×3 + AvgPool×1"]:::pet
    end

    subgraph TOK["Token 化（rearrange）"]
        direction TB
        T_M["b·d·x·y·z → b·(xyz)·d<br/>150 × 128"]:::mri
        T_P["b·d·x·y·z → b·(xyz)·d<br/>150 × 128"]:::pet
    end

    subgraph FUSE["CrossTransformer_MOD_AVG（depth = 3）"]
        direction TB
        L1["Layer 1<br/>mri ← CrossAttn(Q=mri, KV=pet) + mri<br/>pet ← CrossAttn(Q=pet, KV=mri) + pet"]:::fuse
        L2["Layer 2（同上，权重独立）"]:::fuse
        L3["Layer 3（同上，权重独立）"]:::fuse
        POOL["GAP + GMP 池化<br/>mri / pet 各做平均池化 + 最大池化<br/>concat → 4·dim = 512-d cls token"]:::fuse
    end

    HEAD["分类头 fc_cls<br/>Linear 512→512 + LayerNorm + ReLU + Dropout 0.5<br/>Linear 512→64 + LayerNorm + ReLU + Dropout 0.5<br/>Linear 64→2"]:::head
    OUT["Softmax → p(AD)<br/>阈值判定 → CN / AD"]:::head

    MRI --> ENC_M --> T_M --> L1
    PET --> ENC_P --> T_P --> L1
    L1 --> L2 --> L3 --> POOL --> HEAD --> OUT

    subgraph ADV["对抗域对齐分支（梯度反转）"]
        direction LR
        GAP["GAP<br/>AdaptiveAvgPool3d(1)<br/>特征图 → 128-d"]:::adv
        REV["revgrad(x, α=2)<br/>前向恒等 / 反向 ×(−α)"]:::adv
        DISC["域判别器 D<br/>Linear 128→128 + LayerNorm + ReLU<br/>Linear 128→2"]:::adv
        DOM["域标签<br/>MRI 域 y = 1<br/>PET 域 y = 0"]:::adv
    end
    ENC_M -.->|特征图| GAP
    ENC_P -.->|特征图| GAP
    GAP --> REV --> DISC --> DOM

    LOSS["总损失 total = L_cls + L_adv<br/>L_cls = LSCE(logits, y)，权重 1.0<br/>L_adv = 0.1 × [CE(D_MRI,1) + CE(D_PET,0)] / 2"]:::loss
    OUT --> LOSS
    DOM --> LOSS
""".strip()


# ================================================================ 图 2：集成推理链路
def fig_ensemble_pipeline():
    fig, ax = new_fig(19, 11)
    heading(ax,
            "TransMF 快照集成推理链路（ad-screen-backend/services/model_inference.py）",
            "checkpoints/ 全部快照权重 → 逐模型前向 → 概率平均集成 → 温度校准与置信度量化 → CN / AD 决策 → SSE 流式回传前端")

    rows = [(80.0, 12.0), (64.0, 12.0), (48.0, 12.0), (32.0, 12.0), (16.0, 12.0), (2.0, 11.0)]

    stages = [
        ("① 病例影像输入",
         ["MRI NIfTI + PET NIfTI", "路径存在性校验", "影像指纹 (path, mtime, size)"]),
        ("② MONAI 预处理",
         ["LoadImaged / EnsureChannelFirstd", "ScaleIntensityd → [0, 1]",
          "NormalizeIntensityd 零均值 z-score", "EnsureTyped → tensor (1, 1, X, Y, Z)"]),
        ("③ 快照集成前向",
         ["并发安全加载（单例 + RLock）", "CUDA → fp16 autocast / CPU → fp32",
          "N × model_ad(mri, pet)", "取 logits（丢弃对抗分支输出）"]),
        ("④ 概率聚合与校准",
         ["logits / T → Softmax → p(AD)", "概率平均 ens = mean(p_i)",
          "温度 T 在线自校准（Brier 最小）", "统计 probMin / probMax / probStd"]),
        ("⑤ 决策与置信度",
         ["归一化熵 H(p) / log 2", "决策置信度 = 1 − H",
          "阈值：0.5 或 Youden J 校准", "→ CN / AD + 风险等级"]),
        ("⑥ 结果输出与落库",
         ["SSE 流式进度推送前端", "推理日志 + 操作审计", "LRU 缓存回写", "Prometheus 指标上报"]),
    ]

    for (yy, hh), (title, lines) in zip(rows, stages):
        box(ax, 2, yy, 28, hh, title, lines, fc="#FFFFFF", ec=FUSE_EC, ts=10.5, bs=7.6)

    for i in range(len(rows) - 1):
        y_top = rows[i][0]
        y_bot = rows[i + 1][0] + rows[i + 1][1]
        varrow(ax, 16, y_top, y_bot)

    details = [
        ("输入契约",
         ["· 单次推理 = 1 个病例的 1 对 MRI / PET",
          "· 任一影像缺失 → 返回 None，调用方回退模拟数据",
          "· 同一影像重复请求 → 命中 LRU 缓存，30s+ 降至毫秒级"]),
        ("预处理参数",
         ["· ScaleIntensityd：线性缩放到 [0, 1]",
          "· NormalizeIntensityd(nonzero=True, channel_wise=False)",
          "· 推理管道无 SpatialPadd、无增强（与训练 test_transform 一致）"]),
        (None, None),  # 第 3 行用权重矩阵示意图
        ("温度校准与置信度",
         ["· 扫描 T ∈ [1.0, 3.0] 步长 0.1，取 Brier score 最小者",
          "· 每累计 20 条推理样本触发一次在线自校准",
          "· 伪标签 = (ens_prob > 0.5)，半监督、无需人工标注",
          "· 熵 H(p) = −p·log p − (1−p)·log(1−p)，归一化到 [0, 1]"]),
        ("阈值策略",
         ["· 在线默认 0.5；离线用 Youden J = TPR − FPR 选阈值",
          "· ensemble_inference.py 支持 per-fold 与全局阈值两种模式",
          "· 可选用 LogisticRegression 元学习器替代简单概率平均"]),
        ("可观测性",
         ["· 每次推理记录：AD 概率、置信度、熵、模型分歧 std、耗时",
          "· /api/metrics 暴露 Prometheus 指标（observe_inference）",
          "· 推理日志落盘 logs/，供运行审计看板统计"]),
    ]

    for (yy, hh), (title, lines) in zip(rows, details):
        if title is None:
            continue
        box(ax, 33, yy, 39, hh, title, lines, fc=NOTE_FC, ec=NOTE_EC, ts=9.5, bs=7.4)

    # ---- 第 3 行：快照权重矩阵示意 ----
    gy, gh = rows[2][0], rows[2][1]
    box(ax, 33, gy, 39, gh, "快照权重来源：checkpoints/", (),
        fc=NOTE_FC, ec=NOTE_EC, ts=9.5)
    exps = ["V6", "_S2", "_S3", "_S4", "_S5"]
    eps = ["ep40", "ep45", "ep50"]
    gx, cw, ch, cgap = 36.3, 3.9, 1.40, 0.20
    top = gy + gh - 2.6
    for ci, e in enumerate(eps):
        label(ax, gx + ci * (cw + cgap) + cw / 2, top + 0.8, e, size=6.8, color=SUB)
    for ri, ex in enumerate(exps):
        ry = top - (ri + 1) * (ch + cgap)
        label(ax, gx - 0.6, ry + ch / 2, ex, size=6.8, color=SUB, ha="right")
        for ci in range(len(eps)):
            ax.add_patch(FancyBboxPatch(
                (gx + ci * (cw + cgap), ry), cw, ch,
                boxstyle="round,pad=0,rounding_size=0.2", linewidth=0.9,
                edgecolor=FUSE_EC, facecolor="#F3EDFB", zorder=4))
    label(ax, 52.5, gy + gh / 2 - 1.0,
          "每组 = 1 个实验目录：5 折 KFold 训练结果\n"
          "每折保存 3 个 EMA 快照：epoch 40 / 45 / 50\n"
          "候选路径 = 5 × 5 × 3 = 75，实际存在的全部加载\n"
          "（代码注释中称“15 模型集成”，本工作区实为 75 个快照）",
          size=6.9, color="#334155", ha="left", va="center", z=6)

    # ---- 右侧旁路机制 ----
    side = [
        ("鉴权与安全", ["JWT + token 版本号", "登出 / 改密即失效"]),
        ("LRU 推理缓存", ["上限 16 条", "键 = 影像指纹", "命中即返回"]),
        ("模型热重载", ["/model_config 触发", "RLock 互斥", "推理持旧资源快照"]),
        ("设备自适应", ["CUDA → fp16", "CPU → fp32", "无权重 → 模拟推理"]),
        ("集成策略", ["probability_avg", "可选 stacking 元学习器"]),
        ("降级与回退", ["无 checkpoint → 模拟数据", "推理异常 → None 回退", "保证接口不 5xx"]),
    ]
    for (yy, hh), (title, lines) in zip(rows, side):
        box(ax, 75, yy, 23.5, hh, title, lines, fc="#F1F5F9", ec="#94A3B8",
            ts=9.5, bs=7.4)

    save(fig, "02_ensemble_pipeline.png")


MERMAID_02 = """
%% 快照集成推理链路（ad-screen-backend/services/model_inference.py）
flowchart TB
    classDef stage fill:#EAE3F5,stroke:#5B3E8E,stroke-width:1.5px,color:#0F172A
    classDef detail fill:#F5F8FC,stroke:#94A3B8,stroke-width:1px,color:#1E293B
    classDef side fill:#F1F5F9,stroke:#94A3B8,stroke-width:1px,color:#1E293B

    S1["① 病例影像输入<br/>MRI NIfTI + PET NIfTI<br/>影像指纹 path+mtime+size"]:::stage
    S2["② MONAI 预处理<br/>LoadImaged / EnsureChannelFirstd<br/>ScaleIntensityd → NormalizeIntensityd<br/>tensor (1,1,X,Y,Z)"]:::stage
    S3["③ 快照集成前向<br/>N × model_ad(mri, pet)<br/>CUDA fp16 autocast"]:::stage
    S4["④ 概率聚合与校准<br/>logits / T → Softmax → p(AD)<br/>概率平均 + 温度自校准"]:::stage
    S5["⑤ 决策与置信度<br/>归一化熵 / 决策置信度<br/>阈值 0.5 或 Youden J"]:::stage
    S6["⑥ 结果输出与落库<br/>SSE 流式进度 + 日志 + 指标"]:::stage

    S1 --> S2 --> S3 --> S4 --> S5 --> S6

    CKPT["checkpoints/<br/>5 实验目录 × 5 折 × 3 快照<br/>候选路径 75 条，加载全部实际存在的快照"]:::detail
    CKPT --> S3

    CACHE["LRU 推理缓存（上限 16 条）<br/>键 = 影像指纹，命中即返回"]:::side
    S1 <-. 命中 .-> CACHE
    S6 -. 回写 .-> CACHE

    CAL["温度校准<br/>T ∈ [1.0, 3.0] 步长 0.1，Brier 最小<br/>每 20 条样本触发半监督自校准"]:::side
    CAL --> S4

    CONF["置信度量化<br/>归一化熵 H(p)/log2<br/>决策置信度 = 1 − H<br/>brierApprox = 单模型概率方差"]:::side
    CONF --> S5

    OBS["可观测性<br/>Prometheus /api/metrics<br/>推理日志 → 运行审计看板"]:::side
    S6 --> OBS

    FALLBACK["降级路径<br/>无 checkpoint → 模拟推理<br/>推理异常 → 返回 None 由调用方回退"]:::side
    FALLBACK --> S3
""".strip()


# ================================================================ 图 3：系统整体架构
def fig_system_architecture():
    fig, ax = new_fig(19, 13)
    heading(ax,
            "ADScreen 阿尔兹海默病多模态影像智能筛查与干预平台 · 系统架构",
            "Vue 3 前端 → FastAPI 后端 → TransMF 快照集成推理 → SQLite / 文件存储，"
            "覆盖 JWT 鉴权、操作审计、模型热重载与多中心科研分析")

    X0, XW = 1.2, 92.0
    LBL = 12.6  # 左侧层名标签宽度

    # ---------------- 用户与角色 ----------------
    band(ax, X0, 85.0, XW, 8.5, "用户\n与角色")
    roles = [("放射科医师", ["影像阅片 / ROI 标注 / 报告"], MRI_FC, MRI_EC),
             ("神经内科医师", ["AI 诊断 / 随访 / 干预"], PET_FC, PET_EC),
             ("科研管理员", ["数据分析 / 模型监控 / 导出"], FUSE_FC, FUSE_EC),
             ("超级管理员", ["系统管理 / 权限 / 审计"], HEAD_FC, HEAD_EC)]
    cx0 = X0 + LBL + 1.4
    cw = (XW - LBL - 1.4 - 3 * 1.2) / 4
    for i, (t, ls, fc, ec) in enumerate(roles):
        box(ax, cx0 + i * (cw + 1.2), 86.0, cw, 6.5, t, ls, fc=fc, ec=ec, ts=9.5, bs=7.4)

    # ---------------- 前端表现层 ----------------
    band(ax, X0, 68.0, XW, 14.5, "前端\n表现层")
    box(ax, cx0, 69.2, 21.0, 12.1, "技术栈",
        ["Vue 3 + TypeScript + Vite", "Element Plus + Tailwind CSS",
         "ECharts + Three.js", "Pinia + Vue Router + Axios"],
        fc="#E8F1FB", ec="#1D4E89", ts=9.5, bs=7.4)
    mods1 = ["多模态阅片", "AI 辅助诊断", "病例管理", "报告系统", "数据分析", "患者管理"]
    mods2 = ["影像质控", "患者教育", "系统管理", "个人中心", "登录注册", ""]
    mx0 = cx0 + 21.0 + 1.2
    mw = (X0 + XW - mx0 - 5 * 0.9) / 6
    for i, m in enumerate(mods1):
        box(ax, mx0 + i * (mw + 0.9), 75.5, mw, 5.8, None, [m],
            fc="#FFFFFF", ec="#2E6FB7", ts=8.5, bs=7.6, lw=1.2)
    for i, m in enumerate(mods2):
        if not m:
            continue
        box(ax, mx0 + i * (mw + 0.9), 69.2, mw, 5.8, None, [m],
            fc="#FFFFFF", ec="#2E6FB7", ts=8.5, bs=7.6, lw=1.2)

    # ---------------- 通信层 ----------------
    band(ax, X0, 60.5, XW, 6.5, "通信\n与接口层")
    comms = [("Axios 统一封装 + TS 类型", ["/api 前缀，请求/响应拦截"]),
             ("Vite Proxy 开发代理", ["/api → localhost:8000"]),
             ("JWT Bearer Token", ["Authorization 头透传"]),
             ("SSE / EventSource", ["AI 推理流式进度推送"])]
    cw2 = (XW - LBL - 1.4 - 3 * 1.0) / 4
    for i, (t, ls) in enumerate(comms):
        box(ax, cx0 + i * (cw2 + 1.0), 61.3, cw2, 5.0, t, ls,
            fc="#EFF6FF", ec="#2E6FB7", ts=8.4, bs=7.0, lw=1.2)

    # ---------------- 后端服务层 ----------------
    band(ax, X0, 35.5, XW, 23.5, "后端\n服务层\nFastAPI")
    b4 = [("路由层 routers/", ["30+ 业务模块", "病例 / 报告 / 分析 / 管理", "SSE 推理流式接口"]),
          ("鉴权与中间件", ["JWT + token 版本号", "CORS 白名单", "登录防爆破 5 次 / 15 分钟"]),
          ("业务服务层 services/", ["模型推理与集成", "报告生成 / 影像质控", "审计与可观测性"]),
          ("数据模型层", ["SQLAlchemy ORM models/", "Pydantic schemas/", "SQLite 会话管理"]),
          ("安全与合规", ["生产环境强制密钥校验", "操作审计全量落库", "PHI 数据按角色隔离"])]
    bw = (XW - LBL - 1.4 - 4 * 1.0) / 5
    for i, (t, ls) in enumerate(b4):
        box(ax, cx0 + i * (bw + 1.0), 44.0, bw, 14.0, t, ls,
            fc="#FFFFFF", ec=HEAD_EC, ts=9.0, bs=7.2, lw=1.3)
    box(ax, cx0, 36.7, XW - LBL - 1.4, 6.0, "main.py 启动流程",
        ["初始化 SQLite 表结构 → 填充演示数据 → 校验安全配置（production 下默认密钥直接终止）→ 挂载路由与中间件"],
        fc="#F0FDF4", ec=HEAD_EC, ts=9.2, bs=7.4, lw=1.2)

    # ---------------- 数据与存储层 ----------------
    band(ax, X0, 8.5, XW, 12.0, "数据与\n存储层")
    b5 = [("SQLite 数据库", ["ad_screen.db", "用户 / 病例 / 报告", "随访 / 日志 / 配置"]),
          ("影像数据集", ["datasets/ MRI + PET", "ADNI.csv 标签表"]),
          ("模型权重", ["checkpoints/", "5 实验 × 5 折 × 3 快照"]),
          ("日志", ["logs/ 运行日志", "按天滚动，不入库"]),
          ("备份", ["backups/ 数据库备份", "定期快照，不入库"])]
    bw5 = (XW - LBL - 1.4 - 4 * 1.0) / 5
    for i, (t, ls) in enumerate(b5):
        box(ax, cx0 + i * (bw5 + 1.0), 9.7, bw5, 9.6, t, ls,
            fc="#FEF9E7", ec=LOSS_EC, ts=9.0, bs=7.2, lw=1.3)

    # ---------------- AI 推理层 ----------------
    band(ax, X0, 22.0, XW, 12.0, "AI 推理层\nTransMF")
    b6 = [("ModelEnsemble 单例", ["延迟加载 + RLock 互斥", "模型热重载", "设备自适应"]),
          ("TransMF model_ad", ["sNet 3D-CNN 双分支", "CrossTransformer ×3", "对抗域对齐分支"]),
          ("MONAI 预处理", ["LoadImage / 通道优先", "强度归一化 + z-score", "与训练 test_transform 一致"]),
          ("结果后处理", ["概率平均集成", "温度校准 + 置信度熵", "Grad-CAM 热力图 7 类结果"])]
    bw6 = (XW - LBL - 1.4 - 3 * 1.0) / 4
    for i, (t, ls) in enumerate(b6):
        box(ax, cx0 + i * (bw6 + 1.0), 23.2, bw6, 9.6, t, ls,
            fc=ADV_FC, ec=ADV_EC, ts=9.0, bs=7.2, lw=1.3)

    # ---------------- 运维监控层 ----------------
    band(ax, X0, 0.6, XW, 7.0, "运维\n与监控")
    b7 = [("Prometheus 指标", ["/api/metrics 暴露", "prometheus.yml 抓取"]),
          ("运行审计看板", ["登录 / 活跃 / 病例动作", "失败时段统计"]),
          ("健康检查", ["启动自检 + 模型可用性", "无权重自动回退模拟"]),
          ("容器化部署", ["Dockerfile + docker-compose", ".env 注入敏感配置"])]
    bw7 = (XW - LBL - 1.4 - 3 * 1.0) / 4
    for i, (t, ls) in enumerate(b7):
        box(ax, cx0 + i * (bw7 + 1.0), 1.6, bw7, 5.0, t, ls,
            fc="#F1F5F9", ec="#64748B", ts=8.6, bs=7.0, lw=1.2)

    # ---------------- 层间连线（仅在层间空隙内走线，不穿过任何方框） ----------------
    mid = X0 + XW / 2
    varrow(ax, mid, 85.0, 82.5)          # 角色 → 前端
    varrow(ax, mid, 68.0, 67.0)          # 前端 → 通信
    varrow(ax, mid, 60.5, 59.0)          # 通信 → 后端
    varrow(ax, cx0 + 12, 35.5, 34.0)     # 后端 → AI 推理层
    label(ax, cx0 + 13.5, 34.75, "调用推理服务", size=7.0, color=SUB, ha="left")
    varrow(ax, cx0 + 30, 22.0, 20.5)     # AI 推理层 → 数据层（结果 / 日志落盘）
    label(ax, cx0 + 31.5, 21.25, "结果 / 日志落盘", size=7.0, color=SUB, ha="left")
    varrow(ax, cx0 + 56, 20.5, 22.0)     # 数据层 → AI 推理层（加载权重）
    label(ax, cx0 + 57.5, 21.25, "加载 checkpoint 权重", size=7.0, color=SUB, ha="left")
    varrow(ax, cx0 + 12, 8.5, 7.6)       # 数据层 → 运维监控
    label(ax, cx0 + 13.5, 8.05, "指标暴露", size=7.0, color=SUB, ha="left")

    # 后端 ↔ 数据库：右侧旁路通道（跨过 AI 层，避免穿过方框）
    CH = X0 + XW + 2.6
    arrow(ax, (X0 + XW, 40.0), (CH, 40.0), style="-", lw=1.4, color="#64748B")
    arrow(ax, (CH, 40.0), (CH, 15.0), style="-", lw=1.4, color="#64748B")
    arrow(ax, (CH, 15.0), (X0 + XW, 15.0), style="-|>", lw=1.4, color="#64748B")
    label(ax, CH + 1.6, 27.5, "SQLAlchemy ORM：病例 / 报告 / 日志读写",
          size=7.0, color=SUB, rot=90)

    save(fig, "03_system_architecture.png")


MERMAID_03 = """
%% ADScreen 平台整体系统架构
flowchart TB
    classDef fe fill:#E8F1FB,stroke:#1D4E89,stroke-width:1.5px,color:#0F172A
    classDef be fill:#D6EFE6,stroke:#0F6B57,stroke-width:1.5px,color:#0F172A
    classDef db fill:#FEF9E7,stroke:#9A7B0A,stroke-width:1.5px,color:#0F172A
    classDef ai fill:#FBDCD6,stroke:#A93226,stroke-width:1.5px,color:#0F172A
    classDef ops fill:#F1F5F9,stroke:#64748B,stroke-width:1.5px,color:#0F172A
    classDef role fill:#FFFFFF,stroke:#475569,stroke-width:1.5px,color:#0F172A

    subgraph R["用户与角色"]
        direction LR
        R1["放射科医师"]:::role
        R2["神经内科医师"]:::role
        R3["科研管理员"]:::role
        R4["超级管理员"]:::role
    end

    subgraph FE["前端表现层（Vue 3 + TS + Vite）"]
        direction TB
        FE0["Element Plus + Tailwind CSS<br/>ECharts + Three.js<br/>Pinia + Vue Router + Axios"]:::fe
        FE1["多模态阅片 / AI 辅助诊断 / 病例管理<br/>报告系统 / 数据分析 / 患者管理<br/>影像质控 / 患者教育 / 系统管理 / 个人中心 / 登录注册"]:::fe
    end

    COMM["通信层：Axios 封装 + Vite Proxy /api → :8000<br/>JWT Bearer Token + SSE 流式推理进度"]:::fe

    subgraph BE["后端服务层（FastAPI）"]
        direction TB
        BE1["路由层 routers/（30+ 模块）"]:::be
        BE2["鉴权与中间件：JWT + token 版本号 / CORS 白名单 / 登录防爆破"]:::be
        BE3["业务服务层 services/：推理、报告、影像质控、审计"]:::be
        BE4["数据模型层：SQLAlchemy ORM models/ + Pydantic schemas/"]:::be
    end

    subgraph AI["AI 推理层"]
        direction LR
        AI1["ModelEnsemble 单例<br/>延迟加载 + RLock + 热重载"]:::ai
        AI2["TransMF model_ad<br/>sNet + CrossTransformer + 对抗分支"]:::ai
        AI3["MONAI 预处理<br/>强度归一化 + z-score"]:::ai
        AI4["后处理<br/>概率平均 + 温度校准 + Grad-CAM"]:::ai
    end

    subgraph DS["数据与存储层"]
        direction LR
        DS1["SQLite ad_screen.db"]:::db
        DS2["datasets/ MRI + PET + ADNI.csv"]:::db
        DS3["checkpoints/ 75 个快照权重"]:::db
        DS4["logs/ 运行日志"]:::db
        DS5["backups/ 数据库备份"]:::db
    end

    subgraph OPS["运维与监控"]
        direction LR
        OPS1["Prometheus /api/metrics"]:::ops
        OPS2["运行审计看板"]:::ops
        OPS3["健康检查 / 启动自检"]:::ops
        OPS4["Docker + docker-compose"]:::ops
    end

    R --> FE --> COMM --> BE
    BE -->|调用推理服务| AI
    BE -->|读写病例 / 报告| DS
    DS -->|加载快照权重| AI
    BE -->|暴露指标| OPS
""".strip()


# ================================================================ 图 4：训练流程
def fig_training_pipeline():
    fig, ax = new_fig(19, 11)
    heading(ax,
            "TransMF 对抗训练流程（kfold_train_adversarial.py + options/option.py）",
            "队列分层 5 折 KFold → 类别平衡采样与增强 → 双损失对抗训练（分类 + 梯度反转域对齐）→ EMA / 早停 / 快照 → 每折评估与集成交付")

    stages = [
        ("① 数据与标签",
         ["ADNI.csv 标签表", "Subject / Group", "MRI 与 PET 按 subject 配对",
          "ADCN / pMCIsMCI", "MCICN / ALL4"]),
        ("② 队列分层 K 折",
         ["StratifiedKFold", "num_fold = 5", "cohort_split = True 时",
          "按 (ADNI1 / ADNI2+, label)", "4 组分层，降 fold 间方差"]),
        ("③ 采样与增强",
         ["WeightedRandomSampler", "逆频率过采样少数类", "RandFlip / RandRotate",
          "RandZoom(0.95–1)", "可选 Mixup / Gamma"]),
        ("④ 前向传播",
         ["model_ad(mri, pet)", "sNet ×2 编码", "CrossTransformer ×3",
          "输出 logits", "D_MRI / D_PET logits"]),
        ("⑤ 双损失",
         ["L_cls = LSCE", "label_smooth = 0.05", "类别权重逆频率",
          "L_adv = 0.1 × 域对抗", "total = L_cls + L_adv"]),
        ("⑥ 优化与 EMA",
         ["AdamW (5e-4, wd 1e-4)", "Cosine / WarmCos 退火", "梯度裁剪范数 5.0",
          "EMA decay 0.9998", "小 batch 稳定性设计"]),
        ("⑦ 验证与早停",
         ["验证集主指标 AUC", "EarlyStopping", "patience = 15",
          "fixed 模式禁用早停", "快照 epoch 40/45/50"]),
        ("⑧ 汇总与交付",
         ["每折测试集评估", "ACC / AUC / SEN", "SPE / F1 + Youden 阈值",
          "results_summary.csv", "→ 75 快照供集成推理"]),
    ]

    n = len(stages)
    x0, w, gap = 1.5, 11.2, 1.05
    for i, (t, ls) in enumerate(stages):
        x = x0 + i * (w + gap)
        box(ax, x, 62, w, 26, t, ls, fc="#FFFFFF", ec=FUSE_EC, ts=9.2, bs=7.0,
            linespacing=1.65)
        if i < n - 1:
            harrow(ax, x + w, x + w + gap, 75)

    panels = [
        (1.5, 31.0, "数据与增强配置",
         ["· batch_size = 2（3D 医学影像显存受限，小 batch 场景）",
          "· num_workers = 2，pin_memory / non_blocking 开启",
          "· ScaleIntensityd → NormalizeIntensityd(nonzero=True)",
          "· RandFlipd(0.3) / RandRotated(0.3, x±0.05) / RandZoomd(0.3, 0.95–1)",
          "· 可选 Mixup α = 0.4：batch 内插值 + 双标签加权损失",
          "· 可选 Gamma 校正 γ ∈ [0.8, 1.2]，模拟扫描仪对比度差异"]),
        (33.8, 31.0, "损失与优化配置",
         ["· 损失类型：lsce（默认）/ focal(γ=2) / ce，label_smooth 0.05",
          "· 类别平衡：use_class_weight + use_weighted_sampler 双管齐下",
          "· 对抗分支：use_adversarial=True，ad_loss_weight = 0.1",
          "· 优化器：AdamW（β = 0.9 / 0.999，weight_decay = 1e-4）",
          "· 学习率：5e-4，cosine / warmcos / multistep 三种策略",
          "· 梯度裁剪：grad_clip_norm = 5.0，防止 3D 卷积梯度爆炸"]),
        (66.1, 32.4, "训练控制与产物",
         ["· 总 epoch = stage1_epochs(25) + stage2_epochs(25)",
          "· 快照：snapshot_epochs = 40,45,50，保存 EMA 影子权重",
          "· 早停：early_stop_patience = 15，主指标 save_score = auc",
          "· 固定模式：fixed_epochs > 0 时禁用早停与最佳模型选择",
          "· 断点续训：start_fold 指定起始折，保留已有 fold 权重",
          "· 产物：checkpoints/<name>/<fold>/ 快照 + opt.txt + log.txt + results_summary.csv"]),
    ]
    for px, pw, t, ls in panels:
        box(ax, px, 22, pw, 30, t, ls, fc=NOTE_FC, ec=NOTE_EC, ts=10, bs=7.3)

    box(ax, 1.5, 4, 97.0, 14, "每折评估与交付",
        ["测试集指标：ACC / AUC / SEN / SPE / F1（5 折 mean ± std 汇总至 results_summary.csv 与 docs/reports/）",
         "阈值策略：默认 0.5；ensemble_inference.py 支持 per-fold Youden J 与全局 Youden J 校准",
         "集成交付：best_model_v4 / best_model_v6 等目录权重 + checkpoints/ 快照 → ad-screen-backend ModelEnsemble 推理服务"],
        fc="#F0FDF4", ec=HEAD_EC, ts=10, bs=7.4)

    varrow(ax, 50, 22.0, 18.0)

    save(fig, "04_training_pipeline.png")


MERMAID_04 = """
%% TransMF 对抗训练流程（kfold_train_adversarial.py）
flowchart LR
    classDef prep fill:#DCE9F7,stroke:#1D4E89,stroke-width:1.5px,color:#0F172A
    classDef train fill:#EAE3F5,stroke:#5B3E8E,stroke-width:1.5px,color:#0F172A
    classDef ctrl fill:#FBDCD6,stroke:#A93226,stroke-width:1.5px,color:#0F172A
    classDef out fill:#D6EFE6,stroke:#0F6B57,stroke-width:1.5px,color:#0F172A

    D["ADNI.csv 标签表<br/>MRI / PET NIfTI 按 subject 配对"]:::prep
    K["队列分层 5 折 KFold<br/>StratifiedKFold(num_fold=5)<br/>可选 cohort_split 按队列+标签分层"]:::prep
    S["WeightedRandomSampler<br/>逆频率过采样少数类"]:::prep
    A["训练增强<br/>RandFlip / RandRotate / RandZoom<br/>可选 Mixup α=0.4 / Gamma 0.8–1.2"]:::prep

    F["model_ad(mri, pet) 前向<br/>sNet ×2 → CrossTransformer ×3<br/>输出 logits, D_MRI_logits, D_PET_logits"]:::train
    L["双损失<br/>L_cls = LSCE(logits, y)，label_smooth 0.05<br/>L_adv = 0.1 × [CE(D_MRI,1)+CE(D_PET,0)]/2<br/>total = L_cls + L_adv"]:::train
    O["AdamW（lr 5e-4, weight_decay 1e-4）<br/>Cosine / WarmCos / MultiStep 退火<br/>梯度裁剪 5.0"]:::train
    E["EMA（decay 0.9998）<br/>验证 / 测试用影子权重"]:::ctrl
    ES["验证集 AUC 早停<br/>EarlyStopping(patience=15)<br/>fixed 模式禁用早停"]:::ctrl
    SN["快照保存 epoch 40 / 45 / 50<br/>保存 EMA 权重 snapshot_epoch{N}.pt"]:::ctrl

    T["每折测试集评估<br/>ACC / AUC / SEN / SPE / F1"]:::out
    Y["Youden J 阈值校准<br/>per-fold 或全局"]:::out
    CSV["results_summary.csv<br/>5 折 mean ± std"]:::out
    ENS["75 个快照权重<br/>→ ModelEnsemble 集成推理"]:::out

    D --> K --> S --> A --> F --> L --> O --> E --> ES --> SN
    ES --> T --> Y --> CSV
    SN --> T
    SN --> ENS
""".strip()


# ================================================================ main
def main():
    print("生成网络结构图 →", os.path.relpath(OUT, ROOT))
    fig_model_architecture()
    fig_ensemble_pipeline()
    fig_system_architecture()
    fig_training_pipeline()
    save_text("01_model_architecture.mmd", MERMAID_01)
    save_text("02_ensemble_pipeline.mmd", MERMAID_02)
    save_text("03_system_architecture.mmd", MERMAID_03)
    save_text("04_training_pipeline.mmd", MERMAID_04)
    print("完成。")


if __name__ == "__main__":
    main()
