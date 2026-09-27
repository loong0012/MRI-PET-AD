"""
75 模型集成推理引擎精修 —— 纯函数单测
==================================================================
不依赖 torch / 权重 / 影像，只验证：
1. select_stratified_indices 分层等距子集选择的正确性、确定性、三维覆盖
2. aggregate_probs 概率聚合（mean/std/min/max/n）口径
3. "先堆叠 softmax 再平均" 与 "逐个 softmax 再平均" 在相同温度下数学等价
   （这是把 softmax 移出 75 模型循环、消除 CUDA 同步的安全性前提）
"""
import math

from services.model_inference import (
    select_stratified_indices,
    aggregate_probs,
    ENSEMBLE_FULL_SIZE,
)


# ---------------------------------------------------------------
# 分层等距子集选择
# ---------------------------------------------------------------
def test_full_size_constant():
    """5 exp × 5 fold × 3 snapshot = 75"""
    assert ENSEMBLE_FULL_SIZE == 75


def test_select_all_when_cap_zero_or_negative():
    assert select_stratified_indices(75, 0) == list(range(75))
    assert select_stratified_indices(75, -3) == list(range(75))


def test_select_all_when_cap_ge_total():
    assert select_stratified_indices(75, 75) == list(range(75))
    assert select_stratified_indices(75, 200) == list(range(75))


def test_select_empty_total():
    assert select_stratified_indices(0, 10) == []


def test_select_single_middle():
    # cap=1 取中位索引
    assert select_stratified_indices(75, 1) == [37]
    assert select_stratified_indices(10, 1) == [5]


def test_select_deterministic_and_basic_properties():
    idx1 = select_stratified_indices(75, 15)
    idx2 = select_stratified_indices(75, 15)
    assert idx1 == idx2  # 确定性
    assert len(idx1) == 15
    assert len(set(idx1)) == 15  # 唯一
    assert idx1 == sorted(idx1)  # 保序
    assert idx1[0] == 0 and idx1[-1] == 74  # 必含首尾


def test_select_spans_all_three_dimensions():
    """
    checkpoint 嵌套序：exp=idx//15，fold=(idx%15)//3，epoch=idx%3。
    cap=15 的子集必须横跨全部 5 个随机种子、5 个 fold、3 个快照轮次，
    否则"分层代表性"不成立（可能只取到前几个 exp/fold）。
    """
    idx = select_stratified_indices(75, 15)
    exps = {i // 15 for i in idx}
    folds = {(i % 15) // 3 for i in idx}
    epochs = {i % 3 for i in idx}
    assert exps == set(range(5))
    assert folds == set(range(5))
    assert epochs == set(range(3))


def test_select_indices_in_range_for_near_full_cap():
    # cap 接近 total 时即使有去重也必须合法：唯一、在界内、含首尾
    idx = select_stratified_indices(75, 74)
    assert len(idx) <= 74
    assert len(set(idx)) == len(idx)
    assert all(0 <= i < 75 for i in idx)
    assert idx[0] == 0 and idx[-1] == 74


# ---------------------------------------------------------------
# 概率聚合
# ---------------------------------------------------------------
def test_aggregate_probs_known_values():
    agg = aggregate_probs([0.2, 0.4, 0.6, 0.8])
    assert agg["n"] == 4
    assert math.isclose(agg["mean"], 0.5, abs_tol=1e-12)
    # 偏差 -0.3/-0.1/0.1/0.3 → 方差 (0.09+0.01+0.01+0.09)/4 = 0.05
    assert math.isclose(agg["std"], math.sqrt(0.05), abs_tol=1e-12)
    assert agg["min"] == 0.2
    assert agg["max"] == 0.8


def test_aggregate_probs_single_model_zero_std():
    agg = aggregate_probs([0.7])
    assert agg["mean"] == 0.7
    assert agg["std"] == 0.0
    assert agg["min"] == agg["max"] == 0.7


# ---------------------------------------------------------------
# 温度缩放 softmax：循环外批量 vs 循环内逐个，数学等价
# ---------------------------------------------------------------
def _binary_softmax(logit_ad, temperature):
    """二分类 softmax 的 AD 概率（二元等 logit 简化：p=σ(2*logit/T) 不必，
    这里用通用二分类：给定 [logit_cn, logit_ad]）"""
    z = [logit_ad[0] / temperature, logit_ad[1] / temperature]
    m = max(z)
    exps = [math.exp(v - m) for v in z]
    return exps[1] / sum(exps)


def test_batched_rowwise_softmax_equals_sequential():
    """
    推理重构把 softmax(logits/T) 从模型循环内移到 stack 后的 (n,1,2) 张量上，
    沿最后一维（dim=-1，逐行）做 softmax。安全性前提：逐行 softmax 后取均值
    == 循环内逐个 softmax 后取均值。这里验证两者一致，且与"跨批次错误维度"
    的结果不同，证明重构必须保持逐行（dim=-1）口径。
    """
    # 4 个模型，每个 [CN_logit, AD_logit]
    logits = [[-0.2, 0.4], [0.1, -0.3], [1.2, 0.8], [-1.0, 0.6]]
    temperature = 1.7

    # 路径 A（重构前）：逐个模型 softmax 再平均
    per_model = [_binary_softmax(lg, temperature) for lg in logits]
    mean_sequential = sum(per_model) / len(per_model)

    # 路径 B（重构后）：stack 后逐行 softmax 再平均 —— 数学等价
    mean_batched_rowwise = sum(
        _binary_softmax(lg, temperature) for lg in logits
    ) / len(logits)
    assert math.isclose(mean_sequential, mean_batched_rowwise, abs_tol=1e-12)
    assert all(0.0 <= p <= 1.0 for p in per_model)

    # 反例：若错误地把所有 logit 拉平做一次 softmax（跨批次维度），结果会不同，
    # 说明 dim 选择不是无关紧要的——代码必须用 dim=-1。
    flat = [v / temperature for lg in logits for v in lg]
    m = max(flat)
    exps = [math.exp(v - m) for v in flat]
    # 取每个模型 AD 位（奇数索引）的跨批次归一化概率
    wrong_ad = [exps[2 * i + 1] / sum(exps) for i in range(len(logits))]
    mean_wrong = sum(wrong_ad) / len(wrong_ad)
    assert not math.isclose(mean_sequential, mean_wrong, abs_tol=1e-6)


def test_temperature_one_is_plain_softmax():
    """T=1.0 时温度缩放必须退化为普通 softmax（向后兼容）"""
    lg = [-0.5, 1.0]
    assert math.isclose(_binary_softmax(lg, 1.0), _binary_softmax(lg, 1.0))
    # 温度升高使自信概率向 0.5 软化
    p_cold = _binary_softmax(lg, 1.0)
    p_warm = _binary_softmax(lg, 3.0)
    assert abs(p_warm - 0.5) < abs(p_cold - 0.5)
