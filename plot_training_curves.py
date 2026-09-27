"""
TransMF 训练过程可视化脚本
------------------------------------------------------------------
解析 best_model_baseline/fold_*/log.txt 训练日志，生成训练曲线图：
  1. 训练/验证 Loss 曲线（5 fold 平均 ± std 阴影）
  2. 训练/验证 Accuracy 曲线
  3. 验证 AUC / F1 / Balanced-Acc 曲线（early stop 主指标）
  4. 各 fold 最终测试指标对比柱状图
  5. 单 fold 详细曲线（fold_0 为代表）

输出：training_curves/ 下的 PNG 图（300 DPI，可直接用于论文/组会汇报）
"""
import os
import re
import glob
import numpy as np

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# 中文字体（Windows 常用）
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "best_model_baseline")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "training_curves")
os.makedirs(OUT, exist_ok=True)


def parse_log(path):
    """解析单个 fold 的 log.txt，返回 dict of lists（按 epoch 对齐）"""
    train, val = [], []
    cur = None
    with open(path, encoding="utf-8", errors="ignore") as f:
        for line in f:
            m = re.search(r"Training Results - Epoch\[(\d+)\]", line)
            if m:
                cur = {"epoch": int(m.group(1))}
                continue
            if cur is not None and "ce_loss" in line:
                ce = re.search(r"ce_loss:\s*([\d.]+)", line)
                acc = re.search(r"accuracy:\s*([\d.]+)", line)
                if ce:
                    cur["train_loss"] = float(ce.group(1))
                if acc:
                    cur["train_acc"] = float(acc.group(1))
                train.append(cur)
                cur = None
                continue
            if "Validation Results - Epoch" in line:
                cur = {"val": True}
                continue
            if cur is not None and cur.get("val") and "AUC" in line:
                def g(k, default=np.nan):
                    mm = re.search(k + r":\s*([\d.]+)", line)
                    return float(mm.group(1)) if mm else default
                val.append({
                    "loss": g("loss"), "acc": g("accuracy"),
                    "balanced_acc": g("balanced_acc"), "sensitivity": g("sensitivity"),
                    "specificity": g("specificity"), "f1": g("f1 score"), "auc": g("AUC"),
                })
                cur = None
    return train, val


def parse_test_result(path):
    """解析 fold 最终 Test Results"""
    if not os.path.isfile(path):
        return None
    txt = open(path, encoding="utf-8", errors="ignore").read()
    m = re.search(
        r"Test Results\s*\nloss:\s*([\d.]+)\s+accuracy:\s*([\d.]+)\s+balanced_acc:\s*([\d.]+)\s+"
        r"sensitivity:\s*([\d.]+)\s+specificity:\s*([\d.]+)\s+f1 score:\s*([\d.]+)\s+AUC:\s*([\d.]+)",
        txt)
    if not m:
        return None
    keys = ["loss", "acc", "balanced_acc", "sensitivity", "specificity", "f1", "auc"]
    return dict(zip(keys, [float(x) for x in m.groups()]))


def pad(arr, n):
    """将不等长序列补齐到 n（用 nan），便于求 mean/std"""
    out = np.full(n, np.nan)
    out[:len(arr)] = arr
    return out


def main():
    folds = sorted(glob.glob(os.path.join(BASE, "fold_*")))
    folds = [f for f in folds if os.path.isdir(f)]
    print(f"发现 {len(folds)} 个 fold: {[os.path.basename(f) for f in folds]}")

    all_train, all_val, tests = [], [], []
    for fd in folds:
        log = os.path.join(fd, "log.txt")
        if not os.path.isfile(log):
            continue
        tr, va = parse_log(log)
        if not tr or not va:
            continue
        all_train.append(tr)
        all_val.append(va)
        t = parse_test_result(log)
        tests.append(t)
        print(f"  {os.path.basename(fd)}: {len(tr)} epochs 训练, {len(va)} epochs 验证, "
              f"test={'有' if t else '无'}")

    if not all_train:
        print("未解析到任何训练数据，请确认 best_model_baseline 路径正确")
        return

    # 对齐 epoch 数（取最大）
    n_ep = max(len(t) for t in all_train)
    epochs = np.arange(1, n_ep + 1)

    def stack(records, key):
        return np.array([pad([r.get(key, np.nan) for r in rec], n_ep) for rec in records])

    # ---------- 图1：Loss / Acc 曲线（2x2 子图，平均±std） ----------
    fig, axes = plt.subplots(2, 2, figsize=(15, 10))
    metrics = [
        ("train_loss", "训练集 CE Loss", axes[0][0], stack(all_train, "train_loss")),
        ("val_loss", "验证集 Loss", axes[0][1], stack(all_val, "loss")),
        ("train_acc", "训练集 Accuracy", axes[1][0], stack(all_train, "train_acc")),
        ("val_acc", "验证集 Accuracy", axes[1][1], stack(all_val, "acc")),
    ]
    for key, title, ax, data in metrics:
        mean = np.nanmean(data, axis=0)
        std = np.nanstd(data, axis=0)
        ax.plot(epochs, mean, color="#2f6da3", lw=2, label="5-fold 平均")
        ax.fill_between(epochs, mean - std, mean + std, color="#2f6da3", alpha=0.2, label="±1 std")
        ax.set_title(title, fontsize=13, fontweight="bold")
        ax.set_xlabel("Epoch")
        ax.grid(alpha=0.3, ls="--")
        ax.legend()
    fig.suptitle("TransMF 训练过程：Loss 与 Accuracy 曲线（5-fold 交叉验证）", fontsize=15, fontweight="bold")
    fig.tight_layout()
    p1 = os.path.join(OUT, "1_loss_accuracy_curves.png")
    fig.savefig(p1, dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(f"已生成 {p1}")

    # ---------- 图2：验证集分类指标（AUC / F1 / Balanced-Acc / Sens / Spec） ----------
    fig, ax = plt.subplots(figsize=(12, 7))
    val_metrics = [
        ("auc", "AUC（early-stop 主指标）", "#d62728"),
        ("f1", "F1 Score", "#2f6da3"),
        ("balanced_acc", "Balanced Accuracy", "#2ca02c"),
        ("sensitivity", "Sensitivity（敏感性）", "#ff7f0e"),
        ("specificity", "Specificity（特异性）", "#9467bd"),
    ]
    for key, label, color in val_metrics:
        data = stack(all_val, key)
        mean = np.nanmean(data, axis=0)
        ax.plot(epochs, mean, lw=2, label=label, color=color)
    ax.set_title("TransMF 验证集分类指标随训练变化（5-fold 平均）", fontsize=14, fontweight="bold")
    ax.set_xlabel("Epoch")
    ax.set_ylabel("指标值")
    ax.set_ylim(0, 1.0)
    ax.grid(alpha=0.3, ls="--")
    ax.legend(loc="lower right")
    fig.tight_layout()
    p2 = os.path.join(OUT, "2_validation_metrics.png")
    fig.savefig(p2, dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(f"已生成 {p2}")

    # ---------- 图3：各 fold 最终测试指标对比 ----------
    valid_tests = [t for t in tests if t]
    if valid_tests:
        fig, ax = plt.subplots(figsize=(13, 6.5))
        metrics_zh = [("acc", "Accuracy"), ("balanced_acc", "Balanced-Acc"),
                      ("sensitivity", "Sensitivity"), ("specificity", "Specificity"),
                      ("f1", "F1"), ("auc", "AUC")]
        x = np.arange(len(metrics_zh))
        width = 0.8 / len(valid_tests)
        cmap = plt.get_cmap("tab10")
        for i, t in enumerate(valid_tests):
            vals = [t[k] for k, _ in metrics_zh]
            ax.bar(x + i * width - 0.4 + width / 2, vals, width, label=f"fold_{i}", color=cmap(i))
        ax.set_xticks(x)
        ax.set_xticklabels([zh for _, zh in metrics_zh])
        ax.set_ylim(0, 1.0)
        ax.set_ylabel("指标值")
        ax.set_title("TransMF 各 Fold 最终测试集指标对比", fontsize=14, fontweight="bold")
        ax.grid(axis="y", alpha=0.3, ls="--")
        ax.legend(ncol=len(valid_tests), loc="lower right")
        fig.tight_layout()
        p3 = os.path.join(OUT, "3_fold_test_metrics.png")
        fig.savefig(p3, dpi=300, bbox_inches="tight")
        plt.close(fig)
        print(f"已生成 {p3}")

        # 打印汇总表
        print("\n===== 各 fold 测试指标 =====")
        for i, t in enumerate(valid_tests):
            print(f"  fold_{i}: acc={t['acc']:.3f} auc={t['auc']:.3f} f1={t['f1']:.3f} "
                  f"sen={t['sensitivity']:.3f} spe={t['specificity']:.3f}")
        arr = {k: [t[k] for t in valid_tests] for k, _ in metrics_zh}
        print("\n===== 5-fold 平均 ± std =====")
        for k, zh in metrics_zh:
            print(f"  {zh:14s}: {np.mean(arr[k]):.4f} ± {np.std(arr[k]):.4f}")

    # ---------- 图4：fold_0 单 fold 详细曲线（训练 vs 验证） ----------
    tr0, va0 = all_train[0], all_val[0]
    fig, axes = plt.subplots(1, 2, figsize=(14, 5.5))
    ep0 = [r["epoch"] for r in tr0]
    axes[0].plot(ep0, [r.get("train_loss") for r in tr0], label="训练 Loss", color="#2f6da3", lw=2)
    axes[0].plot(range(1, len(va0) + 1), [r["loss"] for r in va0], label="验证 Loss", color="#d62728", lw=2)
    axes[0].set_title("fold_0 Loss 曲线", fontsize=13, fontweight="bold")
    axes[0].set_xlabel("Epoch"); axes[0].grid(alpha=0.3, ls="--"); axes[0].legend()
    axes[1].plot(ep0, [r.get("train_acc") for r in tr0], label="训练 Acc", color="#2f6da3", lw=2)
    axes[1].plot(range(1, len(va0) + 1), [r["acc"] for r in va0], label="验证 Acc", color="#d62728", lw=2)
    axes[1].plot(range(1, len(va0) + 1), [r["auc"] for r in va0], label="验证 AUC", color="#2ca02c", lw=2, ls="--")
    axes[1].set_title("fold_0 Accuracy / AUC 曲线", fontsize=13, fontweight="bold")
    axes[1].set_xlabel("Epoch"); axes[1].set_ylim(0, 1.05); axes[1].grid(alpha=0.3, ls="--"); axes[1].legend()
    fig.suptitle("TransMF fold_0 训练过程详细曲线（过拟合分析参考）", fontsize=14, fontweight="bold")
    fig.tight_layout()
    p4 = os.path.join(OUT, "4_fold0_detail.png")
    fig.savefig(p4, dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(f"已生成 {p4}")

    print(f"\n全部图表输出目录：{OUT}")


if __name__ == "__main__":
    main()
