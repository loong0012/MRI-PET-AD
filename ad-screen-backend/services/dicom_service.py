"""
DICOM → NIfTI 转换服务
- 读取一个 DICOM 序列目录（同一 SeriesInstanceUID 的全部切片）
- 按 ImagePositionPatient / InstanceNumber 排序堆叠为 3D 体数据
- 依据 ImageOrientationPatient / PixelSpacing 构造仿射矩阵
- 输出 NIfTI（.nii.gz），供影像服务统一加载
"""
import os
import logging
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


def _safe_read(path: str):
    """读取单个 DICOM 文件，非 DICOM 返回 None"""
    try:
        import pydicom
        return pydicom.dcmread(path, force=True, stop_before_pixels=False)
    except Exception as e:
        # 目录中常混入非 DICOM 文件（缩略图/文本等），属预期跳过，debug 级记录
        logger.debug("跳过非 DICOM 文件 %s：%s", path, e)
        return None


def _build_affine(slices) -> np.ndarray:
    """由 DICOM 方向/位置信息构造 4×4 仿射矩阵（LPS→RAS 取负）"""
    first = slices[0]
    # 像素间距 (行, 列)
    dy, dx = [float(v) for v in first.PixelSpacing]
    # 层间距：相邻切片 z 坐标差
    iop = [float(v) for v in first.ImageOrientationPatient]
    row_cos = np.array(iop[:3])    # 行方向（X 增加方向）
    col_cos = np.array(iop[3:])    # 列方向（Y 增加方向）
    # 切片法向
    normal = np.cross(row_cos, col_cos)

    pos = [np.array([float(v) for v in s.ImagePositionPatient]) for s in slices]
    if len(slices) > 1:
        dz = float(np.linalg.norm(pos[-1] - pos[0])) / (len(slices) - 1)
    else:
        dz = float(getattr(first, "SliceThickness", 1.0) or 1.0)

    affine = np.eye(4)
    affine[:3, 0] = row_cos * dx
    affine[:3, 1] = col_cos * dy
    affine[:3, 2] = normal * dz
    affine[:3, 3] = pos[0]
    # DICOM 为 LPS 坐标系，NIfTI 为 RAS，X/Y 取负
    lps_to_ras = np.diag([-1, -1, 1, 1])
    return lps_to_ras @ affine


def extract_dicom_meta(dcm_dir: str) -> dict:
    """
    从 DICOM 序列目录抽取 12 个标准标签（首个可读 .dcm 文件）。
    缺失字段返回 None，不抛异常；目录无效/无可读文件 → 返回全 None 字典。
    字段：patientName / patientId / studyDate / studyTime / seriesDescription
         / modality / manufacturer / fieldStrength / windowCenter / windowWidth
         / sliceThickness / pixelSpacing（list[float]，2 元素）
    """
    fields = {
        "patientName": None, "patientId": None,
        "studyDate": None, "studyTime": None,
        "seriesDescription": None, "modality": None,
        "manufacturer": None, "fieldStrength": None,
        "windowCenter": None, "windowWidth": None,
        "sliceThickness": None, "pixelSpacing": None,
    }
    try:
        if not dcm_dir or not os.path.isdir(dcm_dir):
            return fields
        # 找到第一个可读 DICOM 文件
        ds = None
        for name in os.listdir(dcm_dir):
            fpath = os.path.join(dcm_dir, name)
            if not os.path.isfile(fpath):
                continue
            ds = _safe_read(fpath)
            if ds is not None:
                break
        if ds is None:
            return fields
        # PatientName 调 str()（pydicom PersonName 不可直接 JSON 序列化）
        if hasattr(ds, "PatientName"):
            try:
                fields["patientName"] = str(ds.PatientName) or None
            except Exception:
                fields["patientName"] = None
        # 字符串字段直接取
        for src, dst in (
            ("PatientID", "patientId"),
            ("StudyDate", "studyDate"),
            ("StudyTime", "studyTime"),
            ("SeriesDescription", "seriesDescription"),
            ("Modality", "modality"),
            ("Manufacturer", "manufacturer"),
        ):
            v = getattr(ds, src, None)
            if v is not None:
                try:
                    fields[dst] = str(v) or None
                except Exception:
                    fields[dst] = None
        # MagneticFieldStrength → fieldStrength（float）
        mfs = getattr(ds, "MagneticFieldStrength", None)
        if mfs is not None:
            try:
                fields["fieldStrength"] = float(mfs)
            except (TypeError, ValueError):
                fields["fieldStrength"] = None
        # WindowCenter / WindowWidth 可能是 MultiValue，取首个
        wc = getattr(ds, "WindowCenter", None)
        if wc is not None:
            try:
                fields["windowCenter"] = float(wc[0] if hasattr(wc, "__getitem__") else wc)
            except (TypeError, ValueError, IndexError):
                fields["windowCenter"] = None
        ww = getattr(ds, "WindowWidth", None)
        if ww is not None:
            try:
                fields["windowWidth"] = float(ww[0] if hasattr(ww, "__getitem__") else ww)
            except (TypeError, ValueError, IndexError):
                fields["windowWidth"] = None
        # SliceThickness → float
        st = getattr(ds, "SliceThickness", None)
        if st is not None:
            try:
                fields["sliceThickness"] = float(st)
            except (TypeError, ValueError):
                fields["sliceThickness"] = None
        # PixelSpacing → list[float]，2 元素
        ps = getattr(ds, "PixelSpacing", None)
        if ps is not None:
            try:
                vals = [float(v) for v in ps]
                fields["pixelSpacing"] = vals[:2] if len(vals) >= 2 else vals
            except (TypeError, ValueError):
                fields["pixelSpacing"] = None
    except Exception as e:
        logger.warning("抽取 DICOM 元数据失败（%s）：%s", dcm_dir, e)
    return fields


def convert_dicom_series(dcm_dir: str, out_path: str) -> Optional[str]:
    """
    将目录中的 DICOM 序列转换为 NIfTI。
    成功返回 out_path，失败返回 None。
    """
    try:
        import nibabel as nib
    except Exception as e:
        logger.error("DICOM 转换依赖缺失（pydicom/nibabel），无法转换 %s：%s", dcm_dir, e)
        return None

    if not os.path.isdir(dcm_dir):
        return None

    datasets = []
    for name in os.listdir(dcm_dir):
        fpath = os.path.join(dcm_dir, name)
        if not os.path.isfile(fpath):
            continue
        ds = _safe_read(fpath)
        if ds is None or not hasattr(ds, "pixel_array"):
            continue
        datasets.append(ds)

    if not datasets:
        return None

    # 按序列分组，取切片数最多的序列
    groups: dict = {}
    for ds in datasets:
        uid = str(getattr(ds, "SeriesInstanceUID", "default"))
        groups.setdefault(uid, []).append(ds)
    slices = max(groups.values(), key=len)

    # 排序：优先 ImagePositionPatient 法向坐标，回退 InstanceNumber
    def sort_key(ds):
        ipp = getattr(ds, "ImagePositionPatient", None)
        if ipp is not None and len(slices) > 1:
            iop = [float(v) for v in ds.ImageOrientationPatient]
            normal = np.cross(iop[:3], iop[3:])
            return float(np.dot([float(v) for v in ipp], normal))
        return float(getattr(ds, "InstanceNumber", 0) or 0)

    slices.sort(key=sort_key)

    # 堆叠体数据 (行, 列, 层) → 转置为 (X, Y, Z) 由 nibabel 处理
    arr = np.stack([ds.pixel_array for ds in slices], axis=-1).astype(np.float32)

    #  rescale
    slope = float(getattr(slices[0], "RescaleSlope", 1.0) or 1.0)
    intercept = float(getattr(slices[0], "RescaleIntercept", 0.0) or 0.0)
    if slope != 1.0 or intercept != 0.0:
        arr = arr * slope + intercept

    if hasattr(slices[0], "ImageOrientationPatient") and hasattr(slices[0], "ImagePositionPatient"):
        affine = _build_affine(slices)
    else:
        affine = np.eye(4)

    img = nib.Nifti1Image(arr, affine)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    nib.save(img, out_path)
    return out_path
