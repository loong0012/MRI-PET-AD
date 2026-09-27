# -*- coding: utf-8 -*-
"""
训练结果汇总报告脚本
用法:
    python report_results.py                              # 默认扫描 ./checkpoints
    python report_results.py --checkpoints D:/xxx/checkpoints
    python report_results.py --plot                       # 同时绘制每 fold 的 AUC 学习曲线 (需要matplotlib)

输出:
    1) 多实验横向对比表 (loss / acc / balanced_acc / sen / spe / f1 / auc)
    2) 每个实验的每fold明细
    3) (可选) 保存 report.txt 与 csv
"""
import argparse
import csv
import os
import sys
import re
import pathlib
from dataclasses import dataclass, field
from typing import List, Optional, Dict

HEADERS = ['loss', 'acc', 'balanced_acc', 'sen', 'spe', 'f1', 'auc']
_HEADER_ZH = {'loss': 'Loss', 'acc': 'ACC', 'balanced_acc': 'BalAcc',
              'sen': 'SEN(Recall)', 'spe': 'SPE', 'f1': 'F1', 'auc': 'AUC'}


@dataclass
class ExpResult:
    name: str
    path: pathlib.Path
    mean: Dict[str, float] = field(default_factory=dict)
    std: Dict[str, float] = field(default_factory=dict)
    per_fold: List[Dict[str, float]] = field(default_factory=list)
    extras: Dict[str, str] = field(default_factory=dict)   # seed/model/task/...
    # epoch-level log 解析 (可选)
    best_val_per_fold: List[Dict] = field(default_factory=list)


def _safe_float(x) -> float:
    try:
        return float(x)
    except Exception:
        return float('nan')


def parse_csv(csv_path: pathlib.Path) -> Optional[ExpResult]:
    try:
        text = csv_path.read_text(encoding='utf-8-sig')
    except Exception as e:
        print(f"[WARN] 读取CSV失败 {csv_path}: {e}")
        return None

    lines = text.splitlines()
    if not lines:
        return None

    # --- 第一行应该是 fold,loss,acc,balanced_acc,sen,spe,f1,auc  ---
    reader = csv.reader(lines)
    rows = list(reader)
    if not rows:
        return None
    header = [h.strip().lower() for h in rows[0]]
    # 建立列索引
    col_idx = {}
    for h in HEADERS + ['fold']:
        if h in header:
            col_idx[h] = header.index(h)
    # fold rows 必须以 fold_ 开头
    per_fold: List[Dict[str, float]] = []
    mean_row = None
    std_row = None
    extras: Dict[str, str] = {}
    for row in rows[1:]:
        if not row:
            continue
        first = row[0].strip().lower()
        if first.startswith('fold_'):
            d = {}
            for h in HEADERS:
                if h in col_idx and col_idx[h] < len(row):
                    d[h] = _safe_float(row[col_idx[h]])
            per_fold.append(d)
        elif first == 'mean':
            mean_row = row
        elif first == 'std':
            std_row = row
        elif len(row) >= 2:
            extras[row[0].strip()] = row[1].strip()

    # 如果 mean/std 行不存在（旧版CSV），从 per_fold 计算
    import numpy as np
    if per_fold:
        arr = {h: np.asarray([f.get(h, float('nan')) for f in per_fold], dtype=float) for h in HEADERS}
        if mean_row is None:
            mean = {h: float(np.nanmean(arr[h])) for h in HEADERS}
        else:
            mean = {}
            for h in HEADERS:
                if h in col_idx and col_idx[h] < len(mean_row):
                    mean[h] = _safe_float(mean_row[col_idx[h]])
                else:
                    mean[h] = float(np.nanmean(arr[h]))
        if std_row is None:
            std = {h: float(np.nanstd(arr[h])) for h in HEADERS}
        else:
            std = {}
            for h in HEADERS:
                if h in col_idx and col_idx[h] < len(std_row):
                    std[h] = _safe_float(std_row[col_idx[h]])
                else:
                    std[h] = float(np.nanstd(arr[h]))
    else:
        return None

    return ExpResult(name=csv_path.parent.name, path=csv_path.parent,
                     mean=mean, std=std, per_fold=per_fold, extras=extras)


def parse_logs_for_best(exp: ExpResult):
    """尝试从 fold/N/log.txt 中解析 最佳val AUC 和 epoch"""
    import numpy as np
    best = []
    for idx, _ in enumerate(exp.per_fold):
        log = exp.path / str(idx) / 'log.txt'
        info = {'best_val_auc': float('nan'), 'best_epoch': -1, 'epochs': 0}
        if not log.exists():
            best.append(info); continue
        try:
            txt = log.read_text(encoding='utf-8', errors='ignore')
        except Exception:
            best.append(info); continue
        # Validation Results - Epoch[N] ... AUC: x.xxxx
        best_auc = -1.0
        best_ep = -1
        max_ep = 0
        for line in txt.splitlines():
            m = re.search(r'Validation Results - Epoch\[(\d+)\]', line)
            if m:
                max_ep = max(max_ep, int(m.group(1)))
            if 'Validation Results' in line:
                # 下一行或同一行里找 AUC
                pass
            m2 = re.search(r'AUC:\s*([0-9.]+)', line)
            ep_m = re.search(r'Epoch\[(\d+)\]', line)
            if m2 and ep_m and 'Validation' in line:
                try:
                    a = float(m2.group(1))
                    ep = int(ep_m.group(1))
                    if a > best_auc:
                        best_auc = a
                        best_ep = ep
                except Exception:
                    pass
        info['best_val_auc'] = best_auc if best_auc > 0 else float('nan')
        info['best_epoch'] = best_ep
        info['epochs'] = max_ep
        best.append(info)
    exp.best_val_per_fold = best


def _fmt(v, s=None) -> str:
    import math
    if v is None or (isinstance(v, float) and not math.isfinite(v)):
        return "   -   "
    if s is None:
        return f"{v:.4f}"
    if s is not None and (isinstance(s, float) and not math.isfinite(s)):
        return f"{v:.4f}"
    return f"{v:.4f}±{s:.4f}"


def print_report(exps: List[ExpResult]):
    print()
    print("=" * 140)
    print("  TransMF_AD — 训练结果总报告")
    print("=" * 140)
    if not exps:
        print("  (未找到任何 results_summary.csv，请先运行训练)")
        return
    # 总览对比表：每实验一行，每指标 mean±std
    header_row = f"{'实验名':<28} | {'任务/模型':<14} |" + "|".join(
        f"{_HEADER_ZH[h]:>18}" for h in HEADERS)
    print(header_row)
    print("-" * 140)
    rank_key = 'auc'
    for exp in sorted(exps, key=lambda e: e.mean.get(rank_key, -1), reverse=True):
        task = exp.extras.get('task', '-')
        model = exp.extras.get('model', '-')
        tag = f"{task}/{model}"
        line = f"{exp.name:<28} | {tag:<14} |"
        line += "|".join(f"{_fmt(exp.mean.get(h), exp.std.get(h)):>18}" for h in HEADERS)
        print(line)
    print("-" * 140)
    print()

    # 每个实验的 per-fold 明细
    for exp in sorted(exps, key=lambda e: e.mean.get(rank_key, -1), reverse=True):
        print("-" * 100)
        print(f"■ 实验: {exp.name}   (目录: {exp.path})")
        extra_msg = []
        for k in ['seed', 'model', 'task', 'batch_size', 'lr', 'optimizer',
                  'loss_type', 'save_score', 'use_ema', 'early_stop_patience']:
            if k in exp.extras:
                extra_msg.append(f"{k}={exp.extras[k]}")
        if extra_msg:
            print(f"  参数: {', '.join(extra_msg)}")
        print(f"  {'Fold':<8}" + "".join(f"{_HEADER_ZH[h]:>14}" for h in HEADERS))
        for i, f in enumerate(exp.per_fold):
            extra = ""
            if i < len(exp.best_val_per_fold):
                b = exp.best_val_per_fold[i]
                extra = f"  [best_ep={b['best_epoch']}/{b['epochs']}]"
            row = f"  {'fold_'+str(i):<8}" + "".join(f"{_fmt(f.get(h)):>14}" for h in HEADERS)
            print(row + extra)
        # mean/std
        row = f"  {'mean±std':<8}" + "".join(f"{_fmt(exp.mean.get(h), exp.std.get(h)):>14}" for h in HEADERS)
        print(row)
        print()


def save_report_txt(exps: List[ExpResult], out_path: pathlib.Path):
    import io
    old_stdout = sys.stdout
    buf = io.StringIO()
    sys.stdout = buf
    try:
        print_report(exps)
    finally:
        sys.stdout = old_stdout
    out_path.write_text(buf.getvalue(), encoding='utf-8')
    print(f"[IO] 报告已保存 -> {out_path}")


def save_all_comparison_csv(exps: List[ExpResult], out_path: pathlib.Path):
    if not exps:
        return
    with open(out_path, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f)
        w.writerow(['exp_name', 'stat'] + HEADERS)
        for exp in exps:
            w.writerow([exp.name, 'mean'] + [f"{exp.mean.get(h, '')}" for h in HEADERS])
            w.writerow([exp.name, 'std'] + [f"{exp.std.get(h, '')}" for h in HEADERS])
    print(f"[IO] 对比CSV已保存 -> {out_path}")


def plot_curves(exps: List[ExpResult], out_dir: pathlib.Path):
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import numpy as np
    except Exception as e:
        print(f"[WARN] matplotlib 不可用，跳过绘图 ({e})")
        return
    for exp in exps:
        # 从每个 fold 的 log.txt 解析 Validation AUC 曲线
        all_aucs = []
        for idx, _ in enumerate(exp.per_fold):
            log = exp.path / str(idx) / 'log.txt'
            if not log.exists():
                continue
            try:
                txt = log.read_text(encoding='utf-8', errors='ignore')
            except Exception:
                continue
            aucs = []
            for line in txt.splitlines():
                if 'Validation Results' in line or ('AUC:' in line and 'Validation' in line):
                    m = re.search(r'AUC:\s*([0-9.]+)', line)
                    if m:
                        try:
                            aucs.append(float(m.group(1)))
                        except Exception:
                            pass
            # 上面正则只对"Validation Results"整行搜可能匹配不到（AUC和Epoch在相邻两行），重新扫：
            if not aucs:
                aucs = []
                lines = txt.splitlines()
                for i in range(len(lines) - 1):
                    if 'Validation Results' in lines[i]:
                        m = re.search(r'AUC:\s*([0-9.]+)', lines[i] + ' ' + lines[i+1])
                        if m:
                            try:
                                aucs.append(float(m.group(1)))
                            except Exception:
                                pass
            if aucs:
                all_aucs.append(aucs)
        if not all_aucs:
            continue
        # 对齐长度（取最短）
        L = min(len(a) for a in all_aucs)
        arr = np.asarray([a[:L] for a in all_aucs], dtype=float)
        m = arr.mean(axis=0)
        s = arr.std(axis=0)
        x = np.arange(1, L + 1)
        fig, ax = plt.subplots(figsize=(9, 5))
        ax.plot(x, m, 'o-', linewidth=2, label=f'CV Mean AUC (n={arr.shape[0]})')
        ax.fill_between(x, m - s, m + s, alpha=0.2)
        for i, a in enumerate(all_aucs):
            ax.plot(np.arange(1, len(a)+1), a, '--', alpha=0.5, linewidth=1, label=f'fold_{i}')
        ax.set_xlabel('Epoch')
        ax.set_ylabel('Validation AUC')
        ax.set_title(f'{exp.name} — Validation AUC / Epoch')
        ax.grid(True, alpha=0.3)
        ax.legend(fontsize=8)
        out_png = out_dir / f'{exp.name}_auc_curve.png'
        fig.tight_layout()
        fig.savefig(out_png, dpi=140)
        plt.close(fig)
        print(f"[PLOT] {out_png}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--checkpoints', type=str, default='./checkpoints', help='checkpoints 根目录')
    ap.add_argument('--plot', action='store_true', help='是否绘制AUC曲线(需matplotlib)')
    ap.add_argument('--out', type=str, default='', help='输出目录，默认=checkpoints/_reports')
    args = ap.parse_args()

    cp = pathlib.Path(args.checkpoints).resolve()
    if not cp.exists():
        print(f"[错误] checkpoints 目录不存在: {cp}")
        sys.exit(1)

    csvs = list(cp.glob('**/results_summary.csv'))
    print(f"[SCAN] 扫描 {cp}，找到 {len(csvs)} 份 results_summary.csv")
    exps = []
    for c in csvs:
        r = parse_csv(c)
        if r is not None:
            parse_logs_for_best(r)
            exps.append(r)

    print_report(exps)

    # 保存
    out_dir = pathlib.Path(args.out).resolve() if args.out else (cp / '_reports')
    out_dir.mkdir(parents=True, exist_ok=True)
    save_report_txt(exps, out_dir / 'report.txt')
    save_all_comparison_csv(exps, out_dir / 'comparison.csv')
    if args.plot:
        plot_curves(exps, out_dir)


if __name__ == '__main__':
    main()
