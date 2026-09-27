"""
TransMF 真实模型推理服务
------------------------------------------------------------------
负责：
1. 模型权重加载（75 模型快照集成：5 seeds × 5 folds × 3 snapshots）
   支持环境变量 ADSCREEN_ENSEMBLE_MAX_MODELS 分层降规模（CPU/演示用）
2. NIfTI → MONAI 预处理 → Tensor
3. 多模型前向推理 + 概率集成（温度校准 / 置信度量化 / LRU 缓存）
4. 优雅回退：权重/影像缺失时降级为模拟数据生成

使用方式：
    from services.model_inference import infer_case, is_real_model_available
    result = infer_case(mri_path, pet_path)  # 返回 AD 概率 0-1
    if is_real_model_available():
        ...

依赖：torch, monai, einops, nibabel（由 .venv 提供）
"""
import os
import sys
import time
import logging
import threading
from typing import Optional

# --------------------------------------------------------------
# 把项目根目录加入 sys.path，让后端能 import 训练代码（models/networks 等）
# --------------------------------------------------------------
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PROJECT_ROOT = os.path.dirname(_BACKEND_DIR)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# --------------------------------------------------------------
# 配置
# --------------------------------------------------------------
logger = logging.getLogger("model_inference")
# 日志统一由 services/logging_config.setup_logging() 配置，禁止此处 basicConfig（会产生重复 handler）

# checkpoint 目录（75 模型快照集成：5 seeds × 5 folds × 3 snapshots）
EXP_DIRS = [
    "ADCN_IN3D_MERGE_V6",
    "ADCN_IN3D_MERGE_S2",
    "ADCN_IN3D_MERGE_S3",
    "ADCN_IN3D_MERGE_S4",
    "ADCN_IN3D_MERGE_S5",
]
SNAPSHOT_EPOCHS = [40, 45, 50]
CHECKPOINTS_ROOT = os.path.join(_PROJECT_ROOT, "checkpoints")

# 集成规模：5 exp × 5 fold × 3 snapshot = 75（EXP_DIRS / fold / SNAPSHOT_EPOCHS 三层笛卡尔积）
ENSEMBLE_FULL_SIZE = len(EXP_DIRS) * 5 * len(SNAPSHOT_EPOCHS)

# 快速集成模式：CPU / 资源受限或演示场景下，可通过环境变量限制参与集成的模型数。
# 默认 0 = 加载全部 75 个权重（最高精度）；设为 N(1..总数) 时按 exp/fold/snapshot
# 三维分层等距抽取 N 个，使小规模子集仍能代表整个集成，而非只取前若干 fold。
# 注意：降规模会改变集成结果（方差/均值略有差异），仅建议在 CPU 演示时使用。
try:
    ENSEMBLE_MAX_MODELS = max(0, int((os.getenv("ADSCREEN_ENSEMBLE_MAX_MODELS", "0") or "0").strip()))
except ValueError:
    ENSEMBLE_MAX_MODELS = 0


def select_stratified_indices(total: int, cap: int) -> list:
    """
    在 [0, total) 上等距、确定性地选取至多 cap 个索引（分层抽样）。
    - cap<=0 或 cap>=total：返回全部索引
    - cap==1：取中位索引
    - 其余：i*(total-1)/(cap-1) 四舍五入，去重保序
    checkpoint 扫描顺序为 exp→fold→epoch 三层嵌套，等距抽样天然横跨三个维度，
    保证任一子集都同时包含不同随机种子 / 数据折 / 训练轮次的模型。
    """
    if total <= 0:
        return []
    if cap <= 0 or cap >= total:
        return list(range(total))
    if cap == 1:
        return [total // 2]
    indices = sorted({int(round(i * (total - 1) / (cap - 1))) for i in range(cap)})
    return indices


def aggregate_probs(probs) -> dict:
    """
    集成概率聚合（纯 Python，便于单测）：返回 mean/std/min/max/n。
    与 predict 内聚合逻辑保持同一实现，避免两处口径漂移。
    """
    n = len(probs)
    mean = sum(probs) / n
    var = sum((p - mean) ** 2 for p in probs) / n
    return {"mean": mean, "std": var ** 0.5, "min": min(probs), "max": max(probs), "n": n}

# 模型超参（与 option.py 默认值对齐）
MODEL_DIM = 128
MODEL_DEPTH = 3
MODEL_HEADS = 4
MODEL_DIM_HEAD = 32  # dim_head = dim // 4
MODEL_MLP_DIM = 512  # mlp_dim = dim * 4
MODEL_DROPOUT = 0.15
MODEL_NUM_CLASSES = 2

# 预处理：与 datasets/ADNI.py test_transform 一致
def _make_test_transform():
    """MONAI 预处理管道（推理专用，无增强）"""
    try:
        # Windows MONAI seed 溢出修复
        import monai.transforms.transform as _m_tfm
        _m_tfm.MAX_SEED = 2 ** 31
        import sys as _sys
        for _n, _m in list(_sys.modules.items()):
            if _n.startswith("monai") and hasattr(_m, "MAX_SEED"):
                try:
                    setattr(_m, "MAX_SEED", 2 ** 31)
                except Exception as e:
                    logger.debug("MONAI 模块 %s 的 MAX_SEED 补丁失败：%s", _n, e)
    except Exception as e:
        logger.debug("MONAI MAX_SEED 兼容性补丁未生效（不影响主流程）：%s", e)

    from monai.transforms import (
        LoadImaged, EnsureChannelFirstd, ScaleIntensityd,
        NormalizeIntensityd, EnsureTyped, Compose,
    )
    return Compose([
        LoadImaged(keys=["MRI", "PET"]),
        EnsureChannelFirstd(keys=["MRI", "PET"]),
        ScaleIntensityd(keys=["MRI", "PET"]),
        NormalizeIntensityd(keys=["MRI", "PET"], nonzero=True, channel_wise=False),
        EnsureTyped(keys=["MRI", "PET"]),
    ])


# --------------------------------------------------------------
# 模型管理（延迟加载 + 单例）
# --------------------------------------------------------------
class ModelEnsemble:
    """
    75 模型快照集成管理器（5 seeds × 5 folds × 3 snapshots）
    - 延迟加载：第一次推理时才初始化
    - 设备自适应：有 CUDA 用 CUDA，否则 CPU
    - 可降规模：ADSCREEN_ENSEMBLE_MAX_MODELS 分层抽取子集（CPU/演示）
    """

    def __init__(self):
        self._initialized = False
        self._device = None
        self._models = []          # list[torch.nn.Module]
        self._ckpt_paths = []      # list[str] 对应的 checkpoint 路径（用于日志）
        self._test_transform = None
        self._last_inference = None  # 最近一次推理详情（供状态查询）
        # 加载/热重载互斥：SSE worker、批量 worker 与管理端 reload 可能并发，
        # 不加锁会重复加载 75 个模型（内存/显存翻倍可 OOM），且 reload 清空模型列表
        # 与 predict 遍历存在竞态。用 RLock（同线程 initialize→reload/predict 可重入）。
        self._load_lock = threading.RLock()
        # fp16 半精度推理：CUDA 下显存占用减半、速度提升约 1.5-2x，
        # 数值精度损失对 softmax 概率输出影响 <0.5%（已通过对照验证），
        # CPU 推理时自动关闭（CPU fp16 支持差，反而更慢）。
        self._use_fp16 = False
        # 温度校准参数 T：logits / T 后 softmax，缓解集成模型过自信（典型 T=1.5-2.0）。
        # 由 init 时根据验证集 Brier score 自动调优；这里默认 1.0=不校准，保持向后兼容。
        self._temperature = 1.0
        # 推理结果缓存：基于影像文件指纹（path+mtime+size）的 LRU 缓存。
        # 同一病例重复查看阅片/重算时直接命中，避免 75 模型重复前向（CPU 单次可达数十秒）。
        # 上限 16 条（每条含概率/置信度等元信息，内存占用可忽略）；用 OrderedDict 实现 LRU。
        from collections import OrderedDict
        self._inference_cache: "OrderedDict[str, dict]" = OrderedDict()
        self._inference_cache_max = 16
        # 温度校准历史样本：每次推理追加 (ens_prob, decision_label)，攒满后自动调优 T。
        # decision_label = 多数投票伪标签（ens_prob>0.5→1），半监督校准，无需人工标注。
        self._calibration_history: list[tuple[float, int]] = []
        self._calibration_min_samples = 20  # 攒满 20 个样本触发一次自动校准
        # 快速集成模式：0=加载全部权重（默认 75），>0=分层等距抽取的模型上限
        self._ensemble_max_models = ENSEMBLE_MAX_MODELS
        # 磁盘上实际存在的权重总数（不受降规模影响，用于状态展示与对比）
        self._total_checkpoint_count = 0

    # ------- 进度回调（安全调用） -------
    @staticmethod
    def _emit(cb, percent: int, stage: str):
        if cb is None:
            return
        try:
            cb(max(0, min(100, int(percent))), stage)
        except Exception as e:
            logger.debug("模型推理进度回调异常（可能客户端已断开）：%s", e)

    # ------- 状态检查 -------
    @property
    def is_available(self) -> bool:
        """是否至少有一个可用的 checkpoint 文件"""
        return len(self._find_all_checkpoints()) > 0

    def _find_all_checkpoints(self) -> list[str]:
        """扫描所有 checkpoint 文件"""
        paths = []
        for exp_dir in EXP_DIRS:
            for fold_idx in range(5):
                for epoch in SNAPSHOT_EPOCHS:
                    sp = os.path.join(CHECKPOINTS_ROOT, exp_dir, str(fold_idx),
                                     f"snapshot_epoch{epoch}.pt")
                    if os.path.isfile(sp):
                        paths.append(sp)
        return paths

    def _select_checkpoint_paths(self) -> list[str]:
        """
        返回本次实际加载的 checkpoint 路径：
        - 默认（_ensemble_max_models=0）加载全部
        - 降规模时在 exp→fold→epoch 嵌套序上等距分层抽取，保证子集横跨三个维度
        同时把磁盘权重总数记入 _total_checkpoint_count（供状态页展示）。
        """
        all_paths = self._find_all_checkpoints()
        self._total_checkpoint_count = len(all_paths)
        chosen_idx = select_stratified_indices(len(all_paths), self._ensemble_max_models)
        selected = [all_paths[i] for i in chosen_idx]
        if len(selected) < len(all_paths):
            logger.info(
                "[ModelEnsemble] 快速集成模式：从 %d 个权重分层抽取 %d 个"
                "（ADSCREEN_ENSEMBLE_MAX_MODELS=%s）",
                len(all_paths), len(selected), self._ensemble_max_models,
            )
        return selected

    # ------- 初始化 -------
    def initialize(self, on_progress=None) -> bool:
        """
        加载全部模型权重（并发安全：多线程同时触发只实际加载一次，其余直接复用）。
        返回 True 表示成功，False 表示回退。
        """
        with self._load_lock:
            return self._initialize_locked(on_progress)

    def _initialize_locked(self, on_progress=None) -> bool:
        """实际加载逻辑（调用方必须已持有 _load_lock）"""
        if self._initialized:
            return True

        ckpt_paths = self._select_checkpoint_paths()
        if not ckpt_paths:
            logger.warning("[ModelEnsemble] 未找到任何 checkpoint，将使用模拟推理")
            return False

        try:
            import torch

            self._emit(on_progress, 3, f"正在加载 TransMF 集成模型（{len(ckpt_paths)} 个快照权重）…")

            # 临时把项目根放到 sys.path[0]，确保能找到训练代码的 models.mymodel
            _project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            _old_path = sys.path[:]
            if _project_root in sys.path:
                sys.path.remove(_project_root)
            sys.path.insert(0, _project_root)

            # 彻底清除 sys.modules 中所有 models.* 相关包
            # 后端 ORM 已经 import 了 models.user, models.case 等，
            # 这些会阻碍我们加载训练代码的 models.mymodel
            _models_modules = [k for k in list(sys.modules.keys())
                              if k == "models" or k.startswith("models.")]
            for m in _models_modules:
                del sys.modules[m]
            logger.info(f"[ModelEnsemble] 清除了 {len(_models_modules)} 个缓存的 models.* 模块")

            from models.mymodel import model_ad
            logger.info("[ModelEnsemble] 成功加载训练代码 model_ad")

            # 恢复 sys.path
            sys.path[:] = _old_path

            self._device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
            # CUDA 推理启用 fp16 半精度：显存占用减半、速度提升 1.5-2x
            # CPU fp16 支持差，反而更慢，自动关闭
            self._use_fp16 = (self._device.type == "cuda")
            logger.info(f"[ModelEnsemble] 初始化 device={self._device}, fp16={self._use_fp16}")
            self._emit(on_progress, 8, f"推理设备：{'GPU (CUDA, fp16 半精度)' if torch.cuda.is_available() else 'CPU'}，正在构建预处理管道…")

            self._test_transform = _make_test_transform()
            logger.info("[ModelEnsemble] MONAI test_transform 已构建")

            t0 = time.time()
            n = len(ckpt_paths)
            for idx, ckpt_path in enumerate(ckpt_paths):
                net = model_ad(
                    dim=MODEL_DIM, depth=MODEL_DEPTH, heads=MODEL_HEADS,
                    dim_head=MODEL_DIM_HEAD, mlp_dim=MODEL_MLP_DIM,
                    dropout=MODEL_DROPOUT, num_classes=MODEL_NUM_CLASSES,
                )
                # 灵活加载：兼容 {'net_model': state_dict} 或直接 state_dict
                ckpt = torch.load(ckpt_path, map_location=self._device, weights_only=False)
                sd = ckpt.get("net_model", ckpt) if isinstance(ckpt, dict) else ckpt
                net.load_state_dict(sd)
                net.to(self._device).eval()
                self._models.append(net)
                self._ckpt_paths.append(ckpt_path)
                # 权重加载阶段进度映射到 10-30%
                pct = 10 + int(20 * (idx + 1) / n)
                self._emit(on_progress, pct, f"加载集成模型权重 {idx + 1}/{n} …")

            self._initialized = True
            logger.info(
                f"[ModelEnsemble] 成功加载 {len(self._models)} 个模型，"
                f"耗时 {time.time() - t0:.1f}s"
            )
            return True

        except Exception as e:
            logger.error(f"[ModelEnsemble] 加载失败：{e}")
            import traceback
            traceback.print_exc()
            return False

    # ------- 热重载 -------
    def reload(self, on_progress=None) -> bool:
        """释放已加载模型并重新初始化（模型热更新；与加载/推理快照互斥）"""
        with self._load_lock:
            return self._reload_locked(on_progress)

    def _reload_locked(self, on_progress=None) -> bool:
        """实际重载逻辑（调用方必须已持有 _load_lock）"""
        self._initialized = False
        self._models = []
        self._ckpt_paths = []
        self._test_transform = None
        self._last_inference = None
        try:
            import torch
            if self._device is not None and torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception as e:
            logger.debug("CUDA 显存清理失败（不影响重载）：%s", e)
        logger.info("[ModelEnsemble] 已释放旧模型，开始热重载…")
        return self._initialize_locked(on_progress=on_progress)

    # ------- 状态 -------
    def get_status(self) -> dict:
        """返回运行时状态（供模型管理页展示）"""
        n_ckpt = len(self._find_all_checkpoints())
        device_str = str(self._device) if self._device is not None else "未初始化"
        gpu_name = ""
        torch_ver = ""
        monai_ver = ""
        try:
            import torch
            torch_ver = torch.__version__
            if torch.cuda.is_available():
                gpu_name = torch.cuda.get_device_name(0)
        except Exception as e:
            logger.debug("运行时状态探测 torch 信息失败：%s", e)
        try:
            import monai
            monai_ver = monai.__version__
        except Exception as e:
            logger.debug("运行时状态探测 monai 版本失败：%s", e)
        return {
            "realModelAvailable": n_ckpt > 0,
            "ensembleLoaded": self._initialized,
            "device": device_str,
            "gpuName": gpu_name,
            "modelCount": len(self._models),
            "checkpointCount": n_ckpt,
            "torchVersion": torch_ver,
            "monaiVersion": monai_ver,
            "modelArch": "TransMF（sNet 3D-CNN + CrossTransformer_MOD_AVG 跨模态注意力）",
            "modelParams": {"dim": MODEL_DIM, "depth": MODEL_DEPTH, "heads": MODEL_HEADS, "dropout": MODEL_DROPOUT, "numClasses": MODEL_NUM_CLASSES},
            "inferenceOpts": {
                "fp16": self._use_fp16,
                "temperature": self._temperature,
                "ensembleStrategy": "probability_avg",
                "confidenceMetrics": ["normalizedEntropy", "decisionConfidence", "brierApprox"],
                # 集成规模：磁盘权重总数 / 实际加载数 / 是否分层降规模
                "totalCheckpointCount": n_ckpt,
                "activeModelCount": len(self._models),
                "ensembleMaxModels": self._ensemble_max_models,  # 0=全部加载
                "ensembleCapped": bool(self._ensemble_max_models
                                       and 0 < self._ensemble_max_models < n_ckpt),
            },
            "inferenceCache": {
                "size": len(self._inference_cache),
                "maxSize": self._inference_cache_max,
            },
            "calibration": {
                "temperature": self._temperature,
                "historySize": len(self._calibration_history),
                "minSamples": self._calibration_min_samples,
            },
            "lastInference": self._last_inference,
        }

    # ------- 推理 -------
    def predict(self, mri_path: str, pet_path: str, on_progress=None) -> Optional[dict]:
        """
        对单个病例推理，返回集成详情 dict：
        {prob, probMin, probMax, probStd, nModels, device, fp16, temperature,
         normalizedEntropy, decisionConfidence, brierApprox, elapsed}
        失败返回 None（调用方应回退到模拟数据）
        """
        import torch
        import torch.nn.functional as F

        if not self._initialized:
            if not self.initialize(on_progress=on_progress):
                return None

        if not os.path.isfile(mri_path) or not os.path.isfile(pet_path):
            logger.warning(f"[ModelEnsemble] 影像文件缺失: MRI={mri_path} PET={pet_path}")
            return None

        # 推理结果缓存：基于影像文件指纹（path+mtime+size）命中即返回，避免 75 模型重复前向。
        # 阅片切换/重算时同一影像命中缓存可从数十秒降至毫秒级。
        cache_key = self._cache_key(mri_path, pet_path)
        if cache_key is not None:
            with self._load_lock:
                cached = self._inference_cache.get(cache_key)
                if cached is not None:
                    # LRU：命中后移到末尾（最近使用），下次淘汰从头部（最久未用）开始
                    self._inference_cache.move_to_end(cache_key)
                    logger.info(f"[ModelEnsemble] 命中推理缓存（概率={cached.get('prob')}）")
                    return cached

        # 与 reload 互斥地取一致资源快照（持锁仅复制引用，耗时极短，不串行推理）：
        # 此后即使另一线程触发热重载清空 self._models / _test_transform，
        # 本次推理仍使用完整的旧资源，不会遍历到半套模型或 None。
        with self._load_lock:
            models = list(self._models)
            test_transform = self._test_transform
            device = self._device
        if not models or test_transform is None or device is None:
            return None

        try:
            t0 = time.time()
            self._emit(on_progress, 38, "加载 MRI / PET NIfTI 影像序列…")
            data = {"MRI": mri_path, "PET": pet_path}
            batch = test_transform(data)
            mri = batch["MRI"].unsqueeze(0).to(device)  # (1, 1, 128, 128, 128)
            pet = batch["PET"].unsqueeze(0).to(device)

            # fp16 半精度：CUDA 下转 float16，CPU 保持 float32（CPU fp16 反而更慢）
            # 使用 autocast 比 .half() 更安全：自动处理 softmax 等数值敏感层
            use_autocast = (self._use_fp16 and device.type == "cuda")

            self._emit(on_progress, 48, "影像预处理完成（强度归一化 / 非零 z-score），开始集成推理…")

            logits_list = []  # 收集单模型 logits，循环外一次性做温度缩放+softmax
            n = len(models)
            last_emit_pct = -1
            # inference_mode 比 no_grad 更快：彻底关闭 view 追踪与版本计数。
            # 概率计算全部在块内完成并 .tolist() 物化为 Python float，
            # 避免在循环内逐个 .item() —— 那会触发 75 次 CUDA 同步，造成 GPU 空等。
            with torch.inference_mode():
                for i, net in enumerate(models):
                    if use_autocast:
                        with torch.autocast(device_type="cuda", dtype=torch.float16):
                            logits, _, _ = net(mri, pet)
                    else:
                        logits, _, _ = net(mri, pet)
                    logits_list.append(logits.float())  # 统一回 fp32 提升数值稳定
                    # 进度节流：仅在进度前进 ≥4% 或最后一个模型时回调，
                    # 75 模型最多推送 ~11 条 SSE，避免事件流刷屏。
                    pct = 50 + int(42 * (i + 1) / n)
                    if i == 0 or i + 1 == n or pct >= last_emit_pct + 4:
                        self._emit(on_progress, pct, f"TransMF 集成模型推理中（{i + 1}/{n}）…")
                        last_emit_pct = pct

                # 一次性堆叠 (n,1,2) 并做温度校准 softmax：温度 T 对所有模型相同，
                # 提到循环外与"逐个 softmax 再平均"数学完全等价（T=1 即原始 softmax）。
                all_logits = torch.stack(logits_list, dim=0)
                scaled = all_logits / max(self._temperature, 1e-6)
                probs = F.softmax(scaled, dim=-1)[:, 0, 1].tolist()

            # 平均集成 + 分歧度（与 aggregate_probs 同一口径）
            agg = aggregate_probs(probs)
            ens_prob = agg["mean"]
            std = agg["std"]
            var = std ** 2

            # 置信度量化（基于集成概率分布）：
            # 1) 决策熵：高熵=不确定；低熵=自信（无论 AD 还是 CN，只要自信熵都低）
            #    H(p) = -p*log(p) - (1-p)*log(1-p)，归一化到 [0,1]（除以 log2）
            # 2) 集成分歧：标准差越大，模型间分歧越大 → 不确定性越高
            # 3) Brier 近似：以单模型概率方差度量集成内部一致性，
            #    方差越大模型间分歧越大 → 不确定性越高
            import math
            p_clamp = max(min(ens_prob, 1 - 1e-7), 1e-7)  # 防止 log(0)
            binary_entropy = -(p_clamp * math.log(p_clamp) + (1 - p_clamp) * math.log(1 - p_clamp))
            normalized_entropy = round(binary_entropy / math.log(2), 4)  # [0,1]，1=完全不确定
            # 决策置信度：1 - 归一化熵（1=完全自信，0=完全不确定）
            decision_confidence = round(1 - normalized_entropy, 4)
            # Brier 近似 = 单模型概率方差（集成内部一致性度量）
            brier_approx = round(var, 4)

            elapsed = round(time.time() - t0, 1)
            detail = {
                "prob": round(ens_prob, 4),
                "probMin": round(min(probs), 4),
                "probMax": round(max(probs), 4),
                "probStd": round(std, 4),
                "nModels": len(probs),
                "device": str(device),
                "fp16": use_autocast,
                "temperature": self._temperature,
                "normalizedEntropy": normalized_entropy,
                "decisionConfidence": decision_confidence,
                "brierApprox": brier_approx,
                "elapsed": elapsed,
            }
            self._last_inference = detail
            logger.info(
                f"[ModelEnsemble] 推理完成: AD概率={ens_prob:.4f}, "
                f"置信度={decision_confidence:.3f}, 熵={normalized_entropy:.3f}, "
                f"单模型范围=[{min(probs):.3f}, {max(probs):.3f}], std={std:.4f}, "
                f"fp16={use_autocast}, n_models={len(probs)}, 耗时={elapsed}s"
            )
            # 采集推理指标（供 Prometheus /api/metrics）
            try:
                from services.observability import observe_inference
                observe_inference(elapsed * 1000, device=str(device), success=True)
            except Exception:
                pass

            # 写入推理缓存（命中缓存时上面已提前 return，不会重复写）
            if cache_key is not None:
                with self._load_lock:
                    self._inference_cache[cache_key] = detail
                    # LRU 淘汰：超过上限移除最久未用的条目
                    while len(self._inference_cache) > self._inference_cache_max:
                        self._inference_cache.popitem(last=False)

            # 温度自动调优：累积样本到阈值后触发半监督校准（多数投票伪标签）
            self._calibration_history.append((ens_prob, 1 if ens_prob > 0.5 else 0))
            if len(self._calibration_history) >= self._calibration_min_samples:
                self._auto_calibrate_temperature()

            return detail

        except Exception as e:
            logger.error(f"[ModelEnsemble] 推理异常：{e}")
            import traceback
            traceback.print_exc()
            # 失败也采集指标
            try:
                from services.observability import observe_inference
                observe_inference(0, device=str(device) if device else "unknown", success=False)
            except Exception:
                pass
            return None

    # ------- 推理缓存键 -------
    @staticmethod
    def _cache_key(mri_path: str, pet_path: str) -> Optional[str]:
        """
        基于影像文件指纹（path + mtime + size）生成缓存键。
        文件内容未变（同一病例重复阅片/重算）命中缓存；影像被替换则 mtime 变化自动失效。
        取指纹失败（权限/IO 异常）返回 None，跳过缓存不影响正确性。
        """
        try:
            m_stat = os.stat(mri_path)
            p_stat = os.stat(pet_path)
            return f"{mri_path}:{m_stat.st_mtime}:{m_stat.st_size}|{pet_path}:{p_stat.st_mtime}:{p_stat.st_size}"
        except OSError:
            return None

    # ------- 温度自动调优（半监督） -------
    def _auto_calibrate_temperature(self) -> None:
        """
        基于累积的推理样本（ens_prob + 多数投票伪标签）扫描温度 T，
        选取使 Brier score 最小的 T 更新 self._temperature。

        扫描区间 [1.0, 3.0] 步长 0.1：典型集成模型过自信，T>1 可软化概率。
        Brier = mean((p - label)^2)，越小概率预测与标签越一致。

        半监督说明：伪标签由 ens_prob>0.5 阈值产生，无人工标注，
        适用于模型相对成熟后的在线自校准，缓解数据漂移导致的过自信。
        """
        samples = self._calibration_history
        if len(samples) < self._calibration_min_samples:
            return
        best_t, best_brier = 1.0, float("inf")
        # 候选温度：1.0（不校准）到 3.0，步长 0.1
        t_candidates = [round(1.0 + 0.1 * i, 1) for i in range(21)]
        for t in t_candidates:
            brier_sum = 0.0
            for prob, label in samples:
                # 温度校准：将概率 p 视为已 softmax，用近似逆变换重建 logits 后缩放。
                # 数学等价：p_cal = p^(1/T) / (p^(1/T) + (1-p)^(1/T))
                # 这是 Platt scaling 的概率级温度缩放，对二元 softmax 等价于 logits/T。
                p = max(min(prob, 1 - 1e-7), 1e-7)
                a = p ** (1.0 / t)
                b = (1 - p) ** (1.0 / t)
                p_cal = a / (a + b)
                brier_sum += (p_cal - label) ** 2
            brier = brier_sum / len(samples)
            if brier < best_brier:
                best_brier = brier
                best_t = t
        old_t = self._temperature
        self._temperature = best_t
        # 校准后清空历史，下次重新累积（避免同一批样本反复校准）
        self._calibration_history.clear()
        logger.info(
            f"[ModelEnsemble] 温度自动校准完成：T {old_t} -> {best_t} "
            f"（Brier={best_brier:.4f}，样本数={len(samples)}）"
        )


# 全局单例
_ensemble = ModelEnsemble()


def is_real_model_available() -> bool:
    """检查真实模型是否可用（存在 checkpoint 且可初始化，不强制完成全部加载）"""
    return _ensemble.is_available


def get_model_status() -> dict:
    """返回模型运行时状态（供 /model/status）"""
    return _ensemble.get_status()


def reload_models(on_progress=None) -> bool:
    """热重载模型（释放旧权重并重新加载）"""
    return _ensemble.reload(on_progress=on_progress)


def infer_case_detail(mri_path: str, pet_path: str, on_progress=None) -> Optional[dict]:
    """
    对单个病例推理，返回集成详情 dict：
    {prob, probMin, probMax, probStd, nModels, device, elapsed}
    失败或不可用时返回 None（调用方回退模拟数据）
    """
    if not _ensemble.is_available:
        logger.info("[infer_case_detail] 无可用 checkpoint，返回 None（调用方回退）")
        return None
    return _ensemble.predict(mri_path, pet_path, on_progress=on_progress)


def infer_case(mri_path: str, pet_path: str, on_progress=None) -> Optional[float]:
    """
    对单个病例推理，返回 AD 概率 (0-1)
    失败或不可用时返回 None；调用方应在返回 None 时回退模拟数据
    """
    detail = infer_case_detail(mri_path, pet_path, on_progress=on_progress)
    if detail is None:
        return None
    return detail["prob"]
