"""
真实 NIfTI 影像切片服务
==================================================================
将病例关联的真实 MRI / PET NIfTI 体数据渲染为轴位切片 PNG，
供前端阅片视口与报告缩略图显示（与 TransMF 推理使用同一批影像）。

设计要点：
- nibabel 加载，as_closest_canonical 统一到 RAS 标准方向；
- MRI / PET 均为已配准的 128³ 体数据，空间 1:1 对齐，融合可靠；
- 体数据 LRU 缓存（默认 3 例，约 8MB/模态），切片实时渲染；
- 归一化窗在加载时按全脑非零强度百分位一次性计算，保证逐层窗宽稳定；
- 输出 8-bit 单通道 PNG（128 → 256 双线性放大），前端解码后复用
  既有的窗宽窗位 / Jet 伪彩 / 融合叠加渲染管线。

失败安全：任何异常都返回 None / 不可用，由调用方回退到前端合成影像。
"""
import io
import os
import logging
import threading
from collections import OrderedDict
from typing import Optional

import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

# 输出切片边长（与前端 MedicalViewport 的 SLICE_SIZE=256 对齐）
OUT_SIZE = 256
# 体数据 LRU 缓存容量（病例数）
_CACHE_MAX = 3

_cache: "OrderedDict[str, VolumeData]" = OrderedDict()
_cache_lock = threading.Lock()

# --------------------------------------------------------------
# 渲染结果（切片 PNG 字节）LRU 缓存
# --------------------------------------------------------------
# 阅片时医生在层间来回拖动 / 调窗，同一切片会被重复请求（实测单病例可达上百次）。
# 渲染一次切片需经历：切片提取 → 显示窗映射 → PIL 缩放 → PNG 编码，是 CPU 密集操作。
# 体数据缓存（_cache）只缓存了加载后的 numpy 体数据，重复请求同一切片仍会重复渲染。
# 此处缓存渲染好的 PNG 字节流，key 包含 (case, modality, idx, orientation, wc, ww)，
# 命中即直接返回，重复渲染从 ~10ms 降至微秒级。
# 单张 PNG 约 10-30KB，容量 256 张 ≈ 3-8MB 内存，可控。
_PNG_CACHE_MAX = 256
_png_cache: "OrderedDict[tuple, bytes]" = OrderedDict()
_png_cache_lock = threading.Lock()


def _png_cache_get(key: tuple) -> Optional[bytes]:
    with _png_cache_lock:
        if key in _png_cache:
            _png_cache.move_to_end(key)
            return _png_cache[key]
    return None


def _png_cache_put(key: tuple, png: bytes) -> None:
    with _png_cache_lock:
        _png_cache[key] = png
        _png_cache.move_to_end(key)
        while len(_png_cache) > _PNG_CACHE_MAX:
            _png_cache.popitem(last=False)


def png_cache_stats() -> dict:
    """供状态/监控接口查询缓存占用"""
    with _png_cache_lock:
        return {"size": len(_png_cache), "maxSize": _PNG_CACHE_MAX}


class VolumeData:
    """单个病例的 MRI/PET 体数据与显示窗"""

    def __init__(self, mri: Optional[np.ndarray], pet: Optional[np.ndarray]):
        self.mri = mri
        self.pet = pet
        # 体数据形状（RAS canonical: X=左右, Y=前后, Z=上下）
        ref = mri if mri is not None else pet
        self.shape = tuple(ref.shape) if ref is not None else (0, 0, 0)
        # 全脑显示窗（非零强度百分位）
        self.mri_lo, self.mri_hi = _percentile_window(mri) if mri is not None else (0.0, 1.0)
        self.pet_lo, self.pet_hi = _percentile_window(pet) if pet is not None else (0.0, 1.0)

    def slice_count(self, orientation: str = "axial") -> int:
        """按平面方向返回切片层数：axial=Z(shape[2]), sagittal=X(shape[0]), coronal=Y(shape[1])"""
        if orientation == "sagittal":
            return int(self.shape[0])
        if orientation == "coronal":
            return int(self.shape[1])
        return int(self.shape[2])

    def has(self, modality: str) -> bool:
        return (self.mri is not None) if modality == "MRI" else (self.pet is not None)


def _percentile_window(vol: np.ndarray, lo_pct: float = 1.0, hi_pct: float = 99.0):
    """按非零体素强度百分位计算显示窗（剔除脑外黑边与极端值）"""
    try:
        nz = vol[vol > 0]
        if nz.size == 0:
            nz = vol.ravel()
        lo = float(np.percentile(nz, lo_pct))
        hi = float(np.percentile(nz, hi_pct))
        if hi <= lo:
            hi = lo + 1.0
        return lo, hi
    except Exception as e:
        logger.debug("百分位显示窗计算失败，回退默认窗 [0,1]：%s", e)
        return 0.0, 1.0


def _load_canonical(path: str) -> Optional[np.ndarray]:
    """加载 NIfTI 并重排到 RAS 标准方向，返回 float32 体数据"""
    import nibabel as nib

    img = nib.load(path)
    # 体积上限：防止超大 NIfTI（如 512³ fMRI）物化进内存导致 OOM
    # 正常 AD 病例 128³≈2.1M 体素（8MB/float32）；上限 50M 体素≈200MB/模态
    MAX_VOXELS = 50_000_000
    voxels = 1
    for s in img.shape:
        try:
            voxels *= int(s)
        except (TypeError, ValueError):
            voxels = MAX_VOXELS + 1
            break
    if voxels > MAX_VOXELS:
        logger.warning(
            "NIfTI 体积 %s 超过上限 %d 体素，拒绝加载以防 OOM（%s）",
            img.shape, MAX_VOXELS, path,
        )
        return None
    img = nib.as_closest_canonical(img)
    data = np.asarray(img.dataobj, dtype=np.float32)
    return data


def get_volume(case_id: str, mri_path: str, pet_path: str) -> Optional[VolumeData]:
    """
    获取病例体数据（带 LRU 缓存）。
    任一模态文件缺失/加载失败则该模态为 None；两者都失败返回 None。
    """
    with _cache_lock:
        if case_id in _cache:
            _cache.move_to_end(case_id)
            return _cache[case_id]

    mri = None
    pet = None
    try:
        if mri_path and os.path.isfile(mri_path):
            mri = _load_canonical(mri_path)
    except Exception as e:
        logger.warning("病例 %s 的 MRI 加载失败（%s），回退合成影像：%s", case_id, mri_path, e)
        mri = None
    try:
        if pet_path and os.path.isfile(pet_path):
            pet = _load_canonical(pet_path)
    except Exception as e:
        logger.warning("病例 %s 的 PET 加载失败（%s），回退合成影像：%s", case_id, pet_path, e)
        pet = None

    if mri is None and pet is None:
        return None

    vol = VolumeData(mri, pet)
    with _cache_lock:
        _cache[case_id] = vol
        _cache.move_to_end(case_id)
        while len(_cache) > _CACHE_MAX:
            _cache.popitem(last=False)
    return vol


def peek_slice_count(path: str) -> Optional[int]:
    """只读 NIfTI 头获取轴位层数（不加载全体数据，用于 meta 快速响应）"""
    try:
        import nibabel as nib

        img = nib.as_closest_canonical(nib.load(path))
        return int(img.shape[2]) if len(img.shape) >= 3 else None
    except Exception as e:
        logger.warning("读取 NIfTI 层数失败（%s）：%s", path, e)
        return None


def _slice_plane(vol3d: np.ndarray, idx: int, orientation: str = "axial") -> np.ndarray:
    """
    取指定平面切片并转为放射学显示方向。
    canonical RAS: X=左右(L→R), Y=前后(P→A), Z=上下(I→S)。
    三个平面统一用 flipud(plane.T) 使行=空间第二轴(翻转后朝上为正向)、列=第一轴(左为正)。
    - axial:    plane=vol[:,:,idx] (X,Y) → 行=Y(上A下P), 列=X(左R右L)
    - sagittal: plane=vol[idx,:,:] (Y,Z) → 行=Z(上S下I), 列=Y(左A右P)
    - coronal:  plane=vol[:,idx,:] (X,Z) → 行=Z(上S下I), 列=X(左R右L)
    """
    if orientation == "sagittal":
        plane = vol3d[idx, :, :]  # (Y, Z)
    elif orientation == "coronal":
        plane = vol3d[:, idx, :]  # (X, Z)
    else:
        plane = vol3d[:, :, idx]  # (X, Y)
    return np.flipud(plane.T)


def _to_uint8(slice2d: np.ndarray, lo: float, hi: float) -> np.ndarray:
    """按显示窗线性映射到 0-255"""
    out = np.clip((slice2d - lo) / max(1e-6, (hi - lo)), 0.0, 1.0)
    return (out * 255.0).round().astype(np.uint8)


def render_slice_png(
    case_id: str,
    mri_path: str,
    pet_path: str,
    modality: str,
    idx: int,
    orientation: str = "axial",
    wc: Optional[float] = None,
    ww: Optional[float] = None,
) -> Optional[bytes]:
    """
    渲染单张切片为 PNG 字节流。
    modality: 'MRI' | 'PET'
    idx: 切片层号（0 起，越界返回 None 由路由层响应 404，不做静默钳取）
    orientation: 'axial'(轴位) | 'sagittal'(矢状位) | 'coronal'(冠状位)
    wc / ww: 可选窗宽窗位（传入 wc/ww 时按 (wc - ww/2, wc + ww/2) 线性映射，
              覆盖默认的百分位显示窗；任一为 None 或 ww <= 0 时回退默认窗）
    失败返回 None。
    """
    if modality not in ("MRI", "PET"):
        return None
    if orientation not in ("axial", "sagittal", "coronal"):
        orientation = "axial"
    vol = get_volume(case_id, mri_path, pet_path)
    if vol is None or not vol.has(modality):
        return None

    arr3d = vol.mri if modality == "MRI" else vol.pet
    lo, hi = (vol.mri_lo, vol.mri_hi) if modality == "MRI" else (vol.pet_lo, vol.pet_hi)
    # 用户传入窗宽窗位时覆盖默认百分位窗（MPR 工具栏预设/自定义滑块用）
    if wc is not None and ww is not None and ww > 0:
        lo, hi = (wc - ww / 2.0, wc + ww / 2.0)

    n_slices = vol.slice_count(orientation)
    if n_slices <= 0:
        return None
    # 严格范围校验：拒绝负数（numpy 负索引回绕）与上层越界，调用方应保证 idx 合法
    try:
        idx = int(idx)
    except (TypeError, ValueError):
        return None
    if idx < 0 or idx >= n_slices:
        return None

    # PNG 渲染缓存：阅片反复拖动/调窗时同一切片直接命中，避免重复 CPU 密集渲染
    cache_key = (case_id, modality, idx, orientation, lo, hi)
    cached = _png_cache_get(cache_key)
    if cached is not None:
        return cached

    try:
        sl = _slice_plane(arr3d, idx, orientation)
        u8 = _to_uint8(sl, lo, hi)
        img = Image.fromarray(u8, mode="L").resize((OUT_SIZE, OUT_SIZE), Image.BILINEAR)
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=False)
        png = buf.getvalue()
        _png_cache_put(cache_key, png)
        return png
    except Exception as e:
        logger.warning("切片渲染失败 case=%s modality=%s idx=%s orient=%s：%s",
                       case_id, modality, idx, orientation, e)
        return None


def clear_cache():
    """清空体数据缓存与渲染结果缓存（热重载影像时调用）"""
    with _cache_lock:
        _cache.clear()
    with _png_cache_lock:
        _png_cache.clear()


def render_gradcam_png(
    case_id: str,
    mri_path: str,
    pet_path: str,
    idx: int,
    orientation: str = "axial",
) -> Optional[bytes]:
    """
    生成模型可解释性热力图（PET 代谢偏差图）。
    AD 典型表现为后扣带回/楔前叶/颞顶叶低代谢，本函数将 PET 体数据相对
    全脑均值的偏差渲染为伪彩热力图，直观展示模型关注的异常代谢区域。
    输出 256×256 RGBA PNG（半透明热力图，可叠加在 MRI 上）。
    """
    vol = get_volume(case_id, mri_path, pet_path)
    if vol is None or vol.pet is None:
        return None
    if orientation not in ("axial", "sagittal", "coronal"):
        orientation = "axial"

    n_slices = vol.slice_count(orientation)
    if n_slices <= 0:
        return None
    try:
        idx = int(idx)
    except (TypeError, ValueError):
        return None
    if idx < 0 or idx >= n_slices:
        return None

    # Grad-CAM 渲染缓存：同一层热力图重复请求直接命中
    grad_key = ("gradcam", case_id, idx, orientation)
    cached = _png_cache_get(grad_key)
    if cached is not None:
        return cached

    try:
        pet = vol.pet
        # 全脑非零体素统计
        nz = pet[pet > 0]
        if nz.size < 100:
            return None
        mean = float(np.mean(nz))
        std = float(np.std(nz))
        if std < 1e-6:
            return None

        # 取当前层切片
        sl = _slice_plane(pet, idx, orientation)
        # 标准化偏差 z = (v - mean) / std
        z = (sl - mean) / std
        # clip 到 [-2.5, 2.5]
        z_clip = np.clip(z, -2.5, 2.5)
        # 归一化到 0-1，0.5 = 均值；取反使低代谢（AD 特征）→ 暖色（红）
        t = 1.0 - (z_clip + 2.5) / 5.0

        # 伪彩 jet 风格：红=低代谢(AD 特征)，绿=代谢正常，品红=高代谢
        r = np.clip(1.5 * np.abs(t - 0.5) * 2 - 0.5, 0, 1)  # 两端红
        g = np.clip(1.0 - 2.0 * np.abs(t - 0.5), 0, 1)      # 中间绿
        b = np.clip(1.5 * (1.0 - t) - 0.5, 0, 1)           # 低 t 蓝

        # 仅对脑区（PET > 0）上色，背景透明
        mask = sl > 0
        alpha = np.where(mask, 180, 0).astype(np.uint8)
        rgba = np.zeros(sl.shape + (4,), dtype=np.uint8)
        rgba[..., 0] = (r * 255).astype(np.uint8)
        rgba[..., 1] = (g * 255).astype(np.uint8)
        rgba[..., 2] = (b * 255).astype(np.uint8)
        rgba[..., 3] = alpha

        img = Image.fromarray(rgba, mode="RGBA").resize((OUT_SIZE, OUT_SIZE), Image.BILINEAR)
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=False)
        png = buf.getvalue()
        _png_cache_put(grad_key, png)
        return png
    except Exception as e:
        logger.warning("代谢热力图渲染失败 case=%s idx=%s orient=%s：%s",
                       case_id, idx, orientation, e)
        return None


def _largest_component(binary: np.ndarray) -> np.ndarray:
    """仅保留最大连通域（3D）"""
    from scipy import ndimage as ndi

    lab, n = ndi.label(binary)
    if n <= 1:
        return binary
    sizes = np.bincount(lab.ravel())
    sizes[0] = 0
    return lab == int(np.argmax(sizes))


def _brain_mask(vol: np.ndarray, modality: str) -> np.ndarray:
    """
    脑实质掩膜（布尔数组），用于 3D 体绘制去颅骨 / 头皮 / 面颈部。
    实测数据特征：裁剪体含完整头颅（T1 板障/头皮脂肪信号高于脑实质，
    单纯阈值 + 填孔会把颅盖骨环填成实心盘；PET 颅顶采集截断且白质本底低）。
    策略：先取深部脑芯（侵蚀 / 中央盒种子 + 最大连通域），再在强度掩膜内
    受限区域生长——蛛网膜下腔暗隙天然阻断向颅骨/头皮的扩散。
    """
    from scipy import ndimage as ndi

    nz = vol[vol > 0]
    if nz.size == 0:
        return np.zeros(vol.shape, dtype=bool)
    lo, hi = np.percentile(nz, [1, 99])
    thr = lo + (0.10 if modality == "PET" else 0.14) * (hi - lo)
    intense = vol > thr

    if modality == "PET":
        # PET 无颅骨摄取：轻度侵蚀取芯即可断开零星头皮连接
        core = _largest_component(ndi.binary_erosion(intense, iterations=1))
        grow_steps = 6
    else:
        # MRI：中央 50% 盒内侵蚀取深部白质芯，彻底排除颅盖骨/面颈
        s = np.array(vol.shape)
        seed = np.zeros(vol.shape, dtype=bool)
        c1 = (s * 0.25).astype(int)
        c2 = (s * 0.75).astype(int)
        seed[c1[0]:c2[0], c1[1]:c2[1], c1[2]:c2[2]] = True
        core = _largest_component(ndi.binary_erosion(intense & seed, iterations=2))
        grow_steps = 10

    grown = core
    for _ in range(grow_steps):
        grown = ndi.binary_dilation(grown, iterations=1) & intense
    return ndi.binary_fill_holes(grown)


def render_volume_bytes(
    case_id: str,
    mri_path: str,
    pet_path: str,
    modality: str = "MRI",
    target: int = 64,
) -> Optional[tuple]:
    """
    生成供前端 3D 体绘制使用的低分辨率体数据（RAS canonical: X=左右, Y=前后, Z=上下）。
    - 脑实质掩膜去颅骨 / 头皮 / 面颈，3D 只渲染脑；PET/MRI 各自生成掩膜
      （PET 颅顶采集截断，不能用来裁 MRI）；
    - MRI 按 1%-99% 显示窗归一化；
    - PET 按脑内 p75 皮层参考归一化（临床 FDG 皮层参考法）：
      ratio=v/p75，ratio∈[0.3,1.3] → uint8[0,255]，参考点 ratio=1.0 → t≈0.7；
      白质本底 t<0.3 正常，低代谢皮层 t≈0.25-0.52（前端着色器带皮层门控+后验分区标定）；
    - 返回 (F 序 raw_bytes, (nx, ny, nz))，F 序使 X 轴最快，与 WebGL 3D 纹理排布一致。
    """
    if modality not in ("MRI", "PET"):
        return None
    vol = get_volume(case_id, mri_path, pet_path)
    if vol is None or not vol.has(modality):
        return None
    arr3d = vol.mri if modality == "MRI" else vol.pet

    try:
        from scipy.ndimage import zoom

        factors = [target / max(1, s) for s in arr3d.shape]
        small = zoom(arr3d, factors, order=1, mode="nearest")
        # 各模态使用各自的脑掩膜（PET 颅顶采集截断，不能用来裁 MRI；两模态已同网格配准）
        mask_mod = "PET" if modality == "PET" else "MRI"
        mask_small = zoom(_brain_mask(arr3d, mask_mod).astype(np.float32), factors, order=1, mode="nearest") > 0.5

        if modality == "MRI":
            lo, hi = vol.mri_lo, vol.mri_hi
            norm = np.clip((small - lo) / max(1e-6, hi - lo), 0.0, 1.0)
        else:
            # 皮层参考归一化（临床 FDG-PET 以正常皮层摄取为参照）：
            # ref=脑内 p75（≈ 正常灰质），ratio=v/ref；白质本底 ratio 0.2-0.5 属正常，
            # 低代谢皮层 band ≈ 0.55-0.82（见前端着色器 lowM 带门控，白质不染蓝）。
            brain_vals = small[mask_small]
            ref = float(np.percentile(brain_vals, 75)) if brain_vals.size > 0 else 1.0
            if ref <= 0:
                ref = 1.0
            ratio = small / ref
            r_min, r_max = 0.3, 1.3
            norm = np.clip((ratio - r_min) / (r_max - r_min), 0.0, 1.0)

        u8 = (norm * 255.0).round().astype(np.uint8)
        # 掩膜外置零：颅骨 / 头皮 / 面颈部在 3D 中完全透明
        u8[~mask_small] = 0
        u8[small <= 0] = 0
        # 三维纹理要求均匀维度，居中裁切/填充到 target³
        out = np.zeros((target, target, target), dtype=np.uint8)
        sx = min(target, u8.shape[0])
        sy = min(target, u8.shape[1])
        sz = min(target, u8.shape[2])
        out[:sx, :sy, :sz] = u8[:sx, :sy, :sz]
        return out.tobytes(order="F"), (target, target, target)
    except Exception as e:
        logger.warning("低分辨率体数据导出失败 case=%s modality=%s：%s", case_id, modality, e)
        return None
