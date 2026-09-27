"""
MERGE 5 实验训练曲线生成
------------------------------------------------------------------
为当前部署的 5 个 TransMF 模型实验（V6, S2, S3, S4, S5）各生成训练曲线，
并输出跨实验汇总对比图。
"""
import os
import re
import glob
import numpy as np

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "training_curves")
os.makedirs(OUT, exist_ok=True)

EXP_NAMES = {
    "ADCN_IN3D_MERGE_V6": "MERGE-V6",
    "ADCN_IN3D_MERGE_S2": "MERGE-S2",
    "ADCN_IN3D_MERGE_S3": "MERGE-S3",
    "ADCN_IN3D_MERGE_S4": "MERGE-S4",
    "ADCN_IN3D_MERGE_S5": "MERGE-S5",
}
COLORS = ["#2f6da3", "#d62728", "#2ca02c", "#ff7f0e", "#9467bd"]


def parse_log(path):
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
                if ce: cur["train_loss"] = float(ce.group(1))
                if acc: cur["train_acc"] = float(acc.group(1))
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
    out = np.full(n, np.nan)
    out[:len(arr)] = arr
    return out


def stack(records, key):
    n = max(len(r) for r in records)
    return np.array([pad([r.get(key, np.nan) for r in rec], n) for rec in records])


def process_exp(exp_dir, exp_label, color):
    """解析单个实验的 5 fold 数据，返回训练/验证/测试统计"""
    fold_dirs = sorted(
        [os.path.join(exp_dir, d) for d in os.listdir(exp_dir)
         if d.isdigit() and os.path.isdir(os.path.join(exp_dir, d))],
        key=lambda p: int(os.path.basename(p))
    )
    all_train, all_val, tests = [], [], []
    for fd in fold_dirs:
        log = os.path.join(fd, "log.txt")
        if not os.path.isfile(log):
            continue
        tr, va = parse_log(log)
        if not tr:
            continue
        all_train.append(tr)
        all_val.append(va)  # MERGE 实验无验证数据，va 可能为空
        tests.append(parse_test_result(log))
    if not all_train:
        return None
    n_ep = max(len(t) for t in all_train)
    epochs = np.arange(1, n_ep + 1)
    # 无验证数据时用 nan 填充
    def safe_stack(records, key):
        if not records or all(len(r) == 0 for r in records):
            return np.full((len(all_train), n_ep), np.nan)
        return stack(records, key)
    return {
        "label": exp_label, "color": color,
        "n_ep": n_ep, "epochs": epochs,
        "train_loss": stack(all_train, "train_loss"),
        "val_loss": safe_stack(all_val, "loss"),
        "train_acc": stack(all_train, "train_acc"),
        "val_acc": safe_stack(all_val, "acc"),
        "val_auc": safe_stack(all_val, "auc"),
        "val_f1": safe_stack(all_val, "f1"),
        "tests": [t for t in tests if t],
    }


def plot_per_exp(exp_data, idx):
    """为单个实验生成一张汇总图（有验证数据 2x2，否则 1x2 仅训练曲线）"""
    has_val = not np.all(np.isnan(exp_data["val_loss"]))
    c = exp_data["color"]
    ep = exp_data["epochs"]

    def plot_band(ax, data, title):
        m, s = np.nanmean(data, axis=0), np.nanstd(data, axis=0)
        ax.plot(ep, m, color=c, lw=2, label="5-fold mean")
        ax.fill_between(ep, m - s, m + s, color=c, alpha=0.2, label="±1 std")
        ax.set_title(title, fontsize=12, fontweight="bold")
        ax.set_xlabel("Epoch")
        ax.grid(alpha=0.3, ls="--")
        ax.legend()

    if has_val:
        fig, axes = plt.subplots(2, 2, figsize=(15, 10))
        plot_band(axes[0][0], exp_data["train_loss"], f"{exp_data['label']} 训练 Loss (CE)")
        plot_band(axes[0][1], exp_data["val_loss"], f"{exp_data['label']} 验证 Loss")
        plot_band(axes[1][0], exp_data["train_acc"], f"{exp_data['label']} 训练 Accuracy")
        plot_band(axes[1][1], exp_data["val_acc"], f"{exp_data['label']} 验证 Accuracy")
    else:
        fig, axes = plt.subplots(1, 2, figsize=(14, 5))
        plot_band(axes[0], exp_data["train_loss"], f"{exp_data['label']} 训练 Loss (CE)")
        plot_band(axes[1], exp_data["train_acc"], f"{exp_data['label']} 训练 Accuracy")

    fig.suptitle(f"TransMF {exp_data['label']} 训练过程（5-fold CV）", fontsize=14, fontweight="bold")
    fig.tight_layout()
    p = os.path.join(OUT, f"merge_{idx}_{exp_data['label']}_overview.png")
    fig.savefig(p, dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(f"  已生成 {p}")


def plot_cross_exp_comparison(all_exps):
    """跨实验汇总对比图：验证 AUC 曲线 + 测试指标雷达/柱状"""
    # 图A：验证 AUC 曲线（仅当有实验含验证数据时）
    has_val = any(not np.all(np.isnan(e["val_auc"])) for e in all_exps)
    if has_val:
        fig, ax = plt.subplots(figsize=(12, 7))
        for exp in all_exps:
            if np.all(np.isnan(exp["val_auc"])):
                continue
            ep = exp["epochs"]
            m = np.nanmean(exp["val_auc"], axis=0)
            ax.plot(ep, m, lw=2.2, label=exp["label"], color=exp["color"])
        ax.set_title("TransMF MERGE 系列验证 AUC 对比（5 实验 × 5-fold 平均）", fontsize=14, fontweight="bold")
        ax.set_xlabel("Epoch")
        ax.set_ylabel("AUC")
        ax.set_ylim(0, 1.0)
        ax.grid(alpha=0.3, ls="--")
        ax.legend(loc="lower right")
        fig.tight_layout()
        p = os.path.join(OUT, "merge_cross_exp_auc.png")
        fig.savefig(p, dpi=300, bbox_inches="tight")
        plt.close(fig)
        print(f"  已生成 {p}")
    else:
        print("  跳过验证 AUC 对比图（MERGE 实验为 FixedMode，无逐 epoch 验证数据）")

    # 图B：各实验 5-fold 平均测试指标柱状对比
    fig, ax = plt.subplots(figsize=(12, 6))
    metrics = [("acc", "Accuracy"), ("balanced_acc", "Balanced-Acc"),
               ("sensitivity", "Sensitivity"), ("specificity", "Specificity"),
               ("f1", "F1"), ("auc", "AUC")]
    x = np.arange(len(metrics))
    width = 0.8 / len(all_exps)
    for i, exp in enumerate(all_exps):
        tests = exp["tests"]
        if not tests:
            continue
        vals = [np.mean([t[k] for t in tests]) for k, _ in metrics]
        stds = [np.std([t[k] for t in tests]) for k, _ in metrics]
        bars = ax.bar(x + i * width - 0.4 + width / 2, vals, width,
                      label=exp["label"], color=exp["color"], alpha=0.85)
        ax.errorbar(x + i * width - 0.4 + width / 2, vals, yerr=stds,
                    fmt="none", color="black", capsize=2, lw=0.8)
    ax.set_xticks(x)
    ax.set_xticklabels([zh for _, zh in metrics])
    ax.set_ylim(0, 1.0)
    ax.set_ylabel("指标值")
    ax.set_title("MERGE 系列 5 实验测试集指标对比（mean ± std）", fontsize=14, fontweight="bold")
    ax.grid(axis="y", alpha=0.3, ls="--")
    ax.legend(ncol=len(all_exps), loc="lower right")
    fig.tight_layout()
    p = os.path.join(OUT, "merge_cross_exp_test_metrics.png")
    fig.savefig(p, dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(f"  已生成 {p}")

    # 图C：训练 Loss 与 Accuracy 收敛对比（5 实验叠加，验证训练稳定性/可复现性）
    fig, axes = plt.subplots(1, 2, figsize=(15, 5.5))
    for exp in all_exps:
        ep = exp["epochs"]
        tl = np.nanmean(exp["train_loss"], axis=0)
        ta = np.nanmean(exp["train_acc"], axis=0)
        axes[0].plot(ep, tl, lw=2, label=exp["label"], color=exp["color"])
        axes[1].plot(ep, ta, lw=2, label=exp["label"], color=exp["color"])
    axes[0].set_title("训练 CE Loss 收敛对比", fontsize=13, fontweight="bold")
    axes[0].set_xlabel("Epoch"); axes[0].set_ylabel("Loss")
    axes[0].grid(alpha=0.3, ls="--"); axes[0].legend()
    axes[1].set_title("训练 Accuracy 收敛对比", fontsize=13, fontweight="bold")
    axes[1].set_xlabel("Epoch"); axes[1].set_ylabel("Accuracy")
    axes[1].set_ylim(0, 1.05); axes[1].grid(alpha=0.3, ls="--"); axes[1].legend()
    fig.suptitle("MERGE 系列 5 实验训练收敛一致性（验证训练可复现性）", fontsize=14, fontweight="bold")
    fig.tight_layout()
    p = os.path.join(OUT, "merge_cross_exp_train_convergence.png")
    fig.savefig(p, dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(f"  已生成 {p}")


def main():
    all_exps = []
    for i, (dirname, label) in enumerate(EXP_NAMES.items()):
        d = os.path.join(ROOT, "checkpoints", dirname)
        if not os.path.isdir(d):
            print(f"跳过不存在：{dirname}")
            continue
        print(f"\n解析 {dirname} ...")
        data = process_exp(d, label, COLORS[i])
        if data is None:
            print(f"  无有效 fold 数据")
            continue
        all_exps.append(data)
        print(f"  {len(data['tests'])} folds 测试数据")
        plot_per_exp(data, i + 1)

    if not all_exps:
        print("未解析到任何实验数据")
        return

    print(f"\n===== 跨实验汇总对比 =====")
    plot_cross_exp_comparison(all_exps)

    # 打印数值汇总表
    print("\n===== 各实验 5-fold 平均测试指标 =====")
    for exp in all_exps:
        t = exp["tests"]
        if not t:
            continue
        vals = {k: [x[k] for x in t] for k in ["acc", "balanced_acc", "sensitivity", "specificity", "f1", "auc"]}
        print(f"\n{exp['label']}:")
        for k, name in [("acc", "Accuracy"), ("balanced_acc", "Balanced-Acc"),
                        ("sensitivity", "Sensitivity"), ("specificity", "Specificity"),
                        ("f1", "F1"), ("auc", "AUC")]:
            print(f"  {name:14s}: {np.mean(vals[k]):.4f} ± {np.std(vals[k]):.4f}")

    print(f"\n全部图表输出目录：{OUT}")


if __name__ == "__main__":
    main()
