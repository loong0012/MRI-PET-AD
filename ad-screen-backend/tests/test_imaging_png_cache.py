"""
影像切片渲染 PNG LRU 缓存测试
==================================================================
阅片时医生反复拖动切片/调窗，同一切片会被高频重复请求。
渲染一次切片是 CPU 密集操作（切片→显示窗映射→PIL缩放→PNG编码），
本缓存让重复请求直接命中字节流，避免重复渲染。

验证：
1. 缓存命中（同参数二次请求返回相同字节且走缓存）
2. 不同参数（idx/窗宽/模态）不串缓存
3. LRU 容量上限淘汰最久未用项
4. clear_cache 同时清空体数据与 PNG 缓存
"""
import numpy as np
import pytest

from services import imaging_service as svc


@pytest.fixture(autouse=True)
def _clean_cache():
    svc.clear_cache()
    yield
    svc.clear_cache()


def _fake_volume(shape=(8, 8, 8)):
    """注入一个假体数据，跳过真实 NIfTI 加载"""
    rng = np.random.default_rng(42)
    mri = rng.random(shape).astype(np.float32)
    vol = svc.VolumeData(mri, mri.copy())
    svc._cache["CASE_PNG"] = vol
    return vol


def test_slice_png_cache_hit_same_bytes():
    _fake_volume()
    p1 = svc.render_slice_png("CASE_PNG", "", "", "MRI", 3, "axial")
    p2 = svc.render_slice_png("CASE_PNG", "", "", "MRI", 3, "axial")
    assert p1 is not None and p2 is not None
    assert p1 == p2
    # 第二次走缓存：缓存中应有该 key
    assert svc.png_cache_stats()["size"] >= 1


def test_slice_png_cache_key_distinguishes_params():
    _fake_volume()
    a = svc.render_slice_png("CASE_PNG", "", "", "MRI", 3, "axial")
    b = svc.render_slice_png("CASE_PNG", "", "", "MRI", 4, "axial")   # 不同层
    c = svc.render_slice_png("CASE_PNG", "", "", "MRI", 3, "axial", wc=0.5, ww=1.0)  # 不同窗
    # 不同参数结果应不同（字节不同），且各自独立缓存
    assert a != b
    assert a != c
    assert svc.png_cache_stats()["size"] >= 3


def test_png_cache_lru_eviction():
    _fake_volume()
    cap = svc._PNG_CACHE_MAX
    # 填满并超出容量（用不同 idx 制造不同 key，但同一 orientation 层数有限，
    # 用不同 case_id 前缀绕过层数限制）
    for i in range(cap + 20):
        svc._png_cache_put(("c", "MRI", i, "axial", 0.0, 1.0), b"x" * 4)
    assert svc.png_cache_stats()["size"] == cap


def test_clear_cache_clears_both():
    _fake_volume()
    svc.render_slice_png("CASE_PNG", "", "", "MRI", 3, "axial")
    assert svc.png_cache_stats()["size"] >= 1
    svc.clear_cache()
    assert svc.png_cache_stats()["size"] == 0
    assert len(svc._cache) == 0


def test_gradcam_png_cache_hit():
    _fake_volume(shape=(16, 16, 16))  # gradcam 需足够非零体素
    g1 = svc.render_gradcam_png("CASE_PNG", "", "", 5, "axial")
    g2 = svc.render_gradcam_png("CASE_PNG", "", "", 5, "axial")
    assert g1 is not None and g1 == g2
