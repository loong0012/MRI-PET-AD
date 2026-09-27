"""
MPR（多平面重建）切片服务单元测试
==================================================================
覆盖 P11/P13 后端切片接口的窗宽窗位与三平面方向：
- render_slice_png(wc, ww) 与默认窗输出像素值不同
- render_slice_png(wc=None, ww=None) 维持默认窗行为
- render_slice_png 三方向（axial/sagittal/coronal）各返回合法 PNG
- render_slice_png(idx 越界) 返回 None（由路由层 404）
- imaging_slice(ww=0) 路由层返回 400

真实 NIfTI 文件不可用时通过 monkeypatch 替换 services.imaging_service.get_volume
为返回合成体数据的 mock，专注于校验业务逻辑（窗宽窗位、方向、越界、400）。
"""
import io
import os
import tempfile

import numpy as np
import pytest
from PIL import Image

from services import imaging_service as svc


# ---------- 合成体数据（避免依赖真实 NIfTI 文件）----------
class _DummyVolume:
    """合成体数据：shape (X, Y, Z)，MRI/PET 各一份随机数组，显示窗固定 [0,1]"""

    def __init__(self, shape=(64, 64, 64)):
        rng = np.random.RandomState(42)
        self.mri = rng.rand(*shape).astype(np.float32)
        self.pet = rng.rand(*shape).astype(np.float32)
        self.shape = shape
        # 固定显示窗便于断言窗宽窗位差异
        self.mri_lo, self.mri_hi = 0.0, 1.0
        self.pet_lo, self.pet_hi = 0.0, 1.0

    def slice_count(self, orientation: str = "axial") -> int:
        if orientation == "sagittal":
            return int(self.shape[0])
        if orientation == "coronal":
            return int(self.shape[1])
        return int(self.shape[2])

    def has(self, modality: str) -> bool:
        return (self.mri is not None) if modality == "MRI" else (self.pet is not None)


@pytest.fixture
def patched_volume(monkeypatch):
    """用 monkeypatch 替换 get_volume 为合成体数据，跳过 nibabel 依赖"""
    monkeypatch.setattr(svc, "get_volume", lambda *a, **k: _DummyVolume())
    svc.clear_cache()
    yield
    svc.clear_cache()


def _decode_png(png_bytes: bytes) -> np.ndarray:
    """PNG bytes → numpy uint8 灰度数组（与后端 OUT_SIZE 对齐）"""
    img = Image.open(io.BytesIO(png_bytes))
    return np.array(img)


# ---------- 测试 1：窗宽窗位覆盖默认窗，像素值不同 ----------
def test_render_slice_with_window(patched_volume):
    """wc=0.5, ww=0.4 → 输出 PNG 像素与默认窗 [0,1] 不同"""
    png_default = svc.render_slice_png(
        "TEST_MPR", "/tmp/mri.nii", "/tmp/pet.nii", "MRI", 10, "axial"
    )
    png_windowed = svc.render_slice_png(
        "TEST_MPR", "/tmp/mri.nii", "/tmp/pet.nii", "MRI", 10, "axial",
        wc=0.5, ww=0.4
    )
    assert png_default is not None
    assert png_windowed is not None
    arr_def = _decode_png(png_default)
    arr_win = _decode_png(png_windowed)
    assert arr_def.shape == arr_win.shape
    # 窗宽窗位不同时不应整体一致（合成数据有梯度，窗变会有像素差异）
    assert not np.array_equal(arr_def, arr_win), "窗宽窗位变更未引起像素变化"


# ---------- 测试 2：不传 wc/ww 维持默认行为 ----------
def test_render_slice_no_window_default_behavior(patched_volume):
    """wc=None, ww=None → 使用默认百分位窗，返回有效 PNG"""
    png = svc.render_slice_png(
        "TEST_MPR", "/tmp/mri.nii", "/tmp/pet.nii", "MRI", 5, "axial"
    )
    assert png is not None
    # PNG 文件头 magic
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    img = Image.open(io.BytesIO(png))
    assert img.mode == "L"
    assert img.size == (svc.OUT_SIZE, svc.OUT_SIZE)


# ---------- 测试 3：三方向各返回合法 PNG ----------
def test_imaging_slice_three_orientations(patched_volume):
    """axial / sagittal / coronal 各返回合法 PNG 字节流"""
    for o in ("axial", "sagittal", "coronal"):
        png = svc.render_slice_png(
            "TEST_MPR", "/tmp/mri.nii", "/tmp/pet.nii", "MRI", 10, o
        )
        assert png is not None, f"{o} 方向切片渲染失败"
        assert png[:8] == b"\x89PNG\r\n\x1a\n"
        img = Image.open(io.BytesIO(png))
        assert img.size == (svc.OUT_SIZE, svc.OUT_SIZE)


# ---------- 测试 4：idx 越界 → 返回 None（路由层 404） ----------
def test_imaging_slice_out_of_range_returns_none(patched_volume):
    """idx=999 远超 slice_count → render_slice_png 返回 None（由路由 404）"""
    png = svc.render_slice_png(
        "TEST_MPR", "/tmp/mri.nii", "/tmp/pet.nii", "MRI", 999, "axial"
    )
    assert png is None


# ---------- 测试 5：路由层 ww<=0 → 400 ----------
def test_imaging_slice_invalid_ww_returns_400(db, monkeypatch):
    """imaging_slice(ww=0) 路由层返回 400；绕过 nibabel，用临时空文件让 mri_ok=True"""
    import json
    from datetime import datetime
    from models.case import CaseRecord
    from routers.imaging import imaging_slice

    # 临时空文件：os.path.isfile 通过，但 nibabel 加载会失败——
    # 测试 ww<=0 在 render_slice_png 调用前已拦截，不会进入 nibabel
    with tempfile.NamedTemporaryFile(suffix=".nii", delete=False) as f:
        f.write(b"not-a-nifti")
        tmp_path = f.name
    try:
        case = CaseRecord(
            id="TEST_MPR_400",
            patient_json=json.dumps({"patientNo": "P400", "name": "测试400",
                                     "gender": "M", "age": 70}),
            modality="MRI",
            exam_date="2024-01-15",
            department="神经内科",
            status="pending",
            diag_status="待AI分析",
            risk_level=None,
            risk_score=None,
            has_mri=True,
            has_pet=False,
            create_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            cloud_saved=False,
            mri_path=tmp_path,
            pet_path="",
            is_deleted=False,
            cohort="UNKNOWN",
            scanner_info="",
            dicom_meta="{}",
        )
        db.add(case)
        db.commit()

        # 直接调用路由函数（绕过鉴权依赖），ww=0 应在 render 之前返回 400
        resp = imaging_slice("TEST_MPR_400", modality="MRI", idx=0,
                             orientation="axial", wc=40.0, ww=0.0, db=db)
        # FastAPI Response 对象 status_code=400
        assert resp.status_code == 400
    finally:
        if os.path.isfile(tmp_path):
            os.remove(tmp_path)
