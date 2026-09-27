"""
互操作与编排层测试（2026-09-27 第三轮）
==================================================================
覆盖本轮对标调研后落地的三项能力：

1. FHIR R4 映射：Patient / ImagingStudy / Observation / DiagnosticReport / Bundle
2. DICOMweb 只读：QIDO-RS studies/series/instances + WADO-RS metadata + STOW 501
3. 批量任务持久化：内存单例 → SQLite，多 worker 可共享、重启可恢复

设计原则：直接调用服务层函数与路由函数，不依赖 TestClient/pytest-asyncio，
避免 httpx 版本差异导致测试不可用。
"""
import json
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException


# ------------------------------------------------------------------ 1. FHIR
class TestFHIRPatient:
    """Patient 资源映射"""

    @staticmethod
    def _case(case_id="AD260001", name="张三", gender="M", age=70):
        c = MagicMock()
        c.id = case_id
        c.patient_json = json.dumps({"patientNo": "P001", "name": name, "gender": gender, "age": age},
                                    ensure_ascii=False)
        c.modality = "MRI+PET"
        c.exam_date = "2026-09-01 10:30:00"
        c.dicom_meta = "{}"
        c.pet_tracer = "fdg"
        c.risk_level = "mci"
        return c

    def test_patient_basic_fields(self):
        from services.fhir_service import build_patient
        p = build_patient(self._case())
        assert p["resourceType"] == "Patient"
        assert p["id"] == "case-AD260001"
        assert p["gender"] == "male"
        assert p["identifier"][0]["value"] == "P001"
        assert p["name"][0]["text"] == "张三"

    def test_gender_mapping(self):
        from services.fhir_service import build_patient
        assert build_patient(self._case(gender="F"))["gender"] == "female"
        assert build_patient(self._case(gender=""))["gender"] == "unknown"

    def test_birthdate_is_year_only_for_privacy(self):
        """去标识化：birthDate 只到年份（HIPAA 安全港 / 个人信息保护法要求）"""
        from services.fhir_service import build_patient
        p = build_patient(self._case(age=70))
        assert len(p["birthDate"]) == 4, f"birthDate 应为 YYYY，实际 {p['birthDate']}"
        assert p["birthDate"].isdigit()

    def test_invalid_age_omits_birthdate(self):
        from services.fhir_service import build_patient
        assert "birthDate" not in build_patient(self._case(age=None))
        assert "birthDate" not in build_patient(self._case(age=999))


class TestFHIRImagingStudy:
    """ImagingStudy 资源映射"""

    def test_modality_uses_dicom_coding(self):
        from services.fhir_service import build_imaging_study
        c = MagicMock()
        c.id = "AD260002"
        c.modality = "MRI"
        c.exam_date = "2026-08-15"
        c.dicom_meta = "{}"
        c.pet_tracer = ""
        study = build_imaging_study(c)
        coding = study["modality"][0]
        # DICOM 标准编码体系，而非自定义字符串
        assert coding["system"] == "http://dicom.nema.org/resources/ontology/DCM"
        assert coding["code"] == "MR"

    def test_pet_modality_code(self):
        from services.fhir_service import build_imaging_study
        c = MagicMock()
        c.id = "AD260003"
        c.modality = "PET"
        c.exam_date = "2026-08-15 09:00:00"
        c.dicom_meta = "{}"
        c.pet_tracer = "amyloid"
        s = build_imaging_study(c)
        assert s["modality"][0]["code"] == "PT"
        assert "amyloid" in s["description"]

    def test_started_has_timezone(self):
        """FHIR dateTime 必须带时区，否则下游解析失败"""
        from services.fhir_service import build_imaging_study
        c = MagicMock()
        c.id = "AD260004"
        c.modality = "MRI"
        c.exam_date = "2026-08-15 09:00:00"
        c.dicom_meta = "{}"
        c.pet_tracer = "fdg"
        assert build_imaging_study(c)["started"].endswith("+08:00")


class TestFHIRObservationAndReport:
    """Observation 与 DiagnosticReport"""

    RESULT = {
        "riskScore": 72.5, "riskLevel": "mci",
        "stage": "AD源性MCI", "stageCode": "mci-due-to-ad",
        "biologicalStage": "A+T+N+", "confidence": 0.86,
        "hippocampusVolumeL": 3200.0, "hippocampusVolumeR": 3100.0,
        "meanSUV": 1.32, "corticalThickness": 2.4,
        "ventricleVolume": 30000.0, "mtaScore": "2",
        "modelVersion": "TransMF-15ens-v4", "finishTime": "2026-09-01 11:00:00",
    }

    @staticmethod
    def _case():
        c = MagicMock()
        c.id = "AD260001"
        c.patient_json = json.dumps({"patientNo": "P001", "name": "张三", "gender": "M", "age": 70},
                                    ensure_ascii=False)
        c.modality = "MRI+PET"
        c.exam_date = "2026-09-01 10:30:00"
        c.dicom_meta = "{}"
        c.pet_tracer = "fdg"
        c.risk_level = "mci"
        return c

    def test_observations_use_ucum_units(self):
        from services.fhir_service import build_observations
        obs = build_observations(self._case(), self.RESULT)
        by_code = {o["code"]["coding"][0]["code"]: o for o in obs}
        assert by_code["hippocampus-volume-l"]["valueQuantity"]["code"] == "mm3"
        assert by_code["cortical-thickness"]["valueQuantity"]["unit"] == "mm"
        assert by_code["risk-score"]["valueQuantity"]["value"] == 72.5

    def test_mta_is_codeable_concept_not_number(self):
        """MTA 是序数等级，用 valueCodeableConcept 而非数值（语义正确性）"""
        from services.fhir_service import build_observations
        obs = build_observations(self._case(), self.RESULT)
        mta = [o for o in obs if o["code"]["coding"][0]["code"] == "mta-score"][0]
        assert "valueCodeableConcept" in mta
        assert "valueQuantity" not in mta

    def test_missing_metrics_skipped(self):
        """缺失指标不产出空壳 Observation"""
        from services.fhir_service import build_observations
        obs = build_observations(self._case(), {"riskScore": 10})
        codes = [o["code"]["coding"][0]["code"] for o in obs]
        assert codes == ["risk-score"]

    def test_report_contains_conclusion_and_stage(self):
        from services.fhir_service import build_diagnostic_report
        r = build_diagnostic_report(self._case(), self.RESULT)
        assert r["resourceType"] == "DiagnosticReport"
        assert "AD源性MCI" in r["conclusion"]
        assert "86.0%" in r["conclusion"]
        assert r["conclusionCode"][0]["coding"][0]["code"] == "mci-due-to-ad"

    def test_report_references_observations(self):
        """DiagnosticReport.result 必须指向 Observation（FHIR 引用完整性）"""
        from services.fhir_service import build_diagnostic_report
        r = build_diagnostic_report(self._case(), self.RESULT)
        assert r["result"]
        assert all(ref["reference"].startswith("Observation/") for ref in r["result"])

    def test_report_category_uses_loinc_radiology(self):
        from services.fhir_service import build_diagnostic_report
        r = build_diagnostic_report(self._case(), self.RESULT)
        cat = r["category"][0]["coding"][0]
        assert cat["system"] == "http://loinc.org"
        assert cat["code"] == "LP29684-5"

    def test_model_version_tracked_for_audit(self):
        """模型版本需随结果输出，满足 AI 可追溯监管要求"""
        from services.fhir_service import build_diagnostic_report
        r = build_diagnostic_report(self._case(), self.RESULT)
        ext = {e["url"]: e["valueString"] for e in r["extension"]}
        assert ext["urn:ad-screen:StructureDefinition/model-version"] == "TransMF-15ens-v4"

    def test_no_report_without_analysis(self):
        from services.fhir_service import build_diagnostic_report
        assert build_diagnostic_report(self._case(), {}) is None

    def test_bundle_aggregates_resources(self):
        from services.fhir_service import build_bundle
        b = build_bundle([self._case()], {"AD260001": self.RESULT})
        types = [e["resource"]["resourceType"] for e in b["entry"]]
        assert "Patient" in types
        assert "ImagingStudy" in types
        assert "DiagnosticReport" in types
        assert "Observation" in types
        assert b["total"] == len(b["entry"])

    def test_bundle_without_analysis_still_has_patient(self):
        """未分析病例也输出 Patient/ImagingStudy，但不产出结论"""
        from services.fhir_service import build_bundle
        b = build_bundle([self._case()], {})
        types = [e["resource"]["resourceType"] for e in b["entry"]]
        assert "Patient" in types
        assert "DiagnosticReport" not in types


# ------------------------------------------------------------------ 2. DICOMweb
class TestDICOMWeb:
    """DICOMweb 只读接口"""

    @staticmethod
    def _case(case_id="AD260001", modality="MRI+PET"):
        c = MagicMock()
        c.id = case_id
        c.modality = modality
        c.exam_date = "2026-09-01 10:30:00"
        c.department = "神经内科"
        c.dicom_meta = json.dumps({
            "patientName": "张三", "patientId": "P001",
            "studyDate": "20260901", "studyTime": "103000",
            "modality": "MR", "manufacturer": "SIEMENS",
            "fieldStrength": 3.0, "sliceThickness": 1.2,
            "pixelSpacing": [0.9, 0.9], "windowCenter": 40, "windowWidth": 80,
        }, ensure_ascii=False)
        c.patient_json = json.dumps({"patientNo": "P001", "name": "张三", "gender": "M", "age": 70},
                                    ensure_ascii=False)
        return c

    def test_study_has_required_tags(self):
        from services.dicomweb_service import study_resource
        s = study_resource(self._case())
        # StudyInstanceUID / PatientName / PatientID / ModalitiesInStudy
        assert "0020000D" in s and s["0020000D"]["vr"] == "UI"
        assert "00100010" in s
        assert "00080061" in s  # ModalitiesInStudy
        assert set(s["00080061"]["Value"]) == {"MR", "PT"}

    def test_uid_is_deterministic(self):
        """同一病例每次生成相同 UID，否则外部系统无法稳定引用"""
        from services.dicomweb_service import _dicom_uid
        assert _dicom_uid("study", "AD260001") == _dicom_uid("study", "AD260001")
        assert _dicom_uid("study", "AD260001") != _dicom_uid("study", "AD260002")

    def test_uid_fits_dicom_length_limit(self):
        """DICOM UID 上限 64 字符"""
        from services.dicomweb_service import _dicom_uid
        uid = _dicom_uid("instance", "AD260001", "MR", "1")
        assert len(uid) <= 64, f"UID 超长：{uid}"

    def test_patient_name_uses_pn_structure(self):
        """PN VR 的 Value 是 {Alphabetic: ...} 而非字符串"""
        from services.dicomweb_service import study_resource
        s = study_resource(self._case())
        assert s["00100010"]["Value"][0]["Alphabetic"] == "张三"

    def test_empty_value_emits_vr_only(self):
        """空元素按标准只输出 {vr}"""
        from services.dicomweb_service import study_resource
        c = self._case()
        c.department = ""
        s = study_resource(c)
        assert s["00080080"] == {"vr": "LO"}

    def test_series_resource(self):
        from services.dicomweb_service import series_resource
        s = series_resource(self._case(), "MR", 1)
        assert s["00080060"]["Value"] == ["MR"]
        assert s["00200011"]["Value"] == [1]

    def test_instance_metadata_includes_sop_class(self):
        from services.dicomweb_service import instance_resource
        inst = instance_resource(self._case(), "MR")
        assert inst["00080016"]["Value"] == ["1.2.840.10008.5.1.4.1.1.4"]  # MR Image Storage
        assert inst["00280030"]["Value"] == [0.9, 0.9]

    def test_stow_returns_501(self):
        """写入未实现，显式 501 而非静默成功"""
        from routers.dicomweb import stow_not_implemented
        with pytest.raises(HTTPException) as ei:
            stow_not_implemented()
        assert ei.value.status_code == 501


# ------------------------------------------------------------------ 3. 批量任务持久化
class TestBatchTaskPersistence:
    """批量任务从内存单例改为数据库持久化"""

    def test_task_survives_new_manager_instance(self, tmp_path, monkeypatch):
        """新 manager 实例（模拟另一 worker/进程）能读到同一任务"""
        import database
        from sqlalchemy import create_engine
        from sqlalchemy import event
        from sqlalchemy.orm import sessionmaker
        import services.batch_task as bt

        engine = create_engine(f"sqlite:///{tmp_path}/tasks.db",
                               connect_args={"check_same_thread": False})
        event.listen(engine, "connect", database._sqlite_pragmas)
        database.Base.metadata.create_all(bind=engine)
        Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        monkeypatch.setattr(bt, "SessionLocal", Session)

        m1 = bt.BatchTaskManager()
        tid = m1.create_task(["AD260001", "AD260002"], "rad01")

        m2 = bt.BatchTaskManager()  # 模拟另一个进程
        task = m2.get_task(tid)
        assert task is not None, "任务未持久化：新实例读不到"
        assert task.total == 2
        assert task.operator == "rad01"
        assert task.status == "pending"

    def test_progress_and_finish_persisted(self, tmp_path, monkeypatch):
        import database
        from sqlalchemy import create_engine, event
        from sqlalchemy.orm import sessionmaker
        import services.batch_task as bt

        engine = create_engine(f"sqlite:///{tmp_path}/t2.db", connect_args={"check_same_thread": False})
        event.listen(engine, "connect", database._sqlite_pragmas)
        database.Base.metadata.create_all(bind=engine)
        monkeypatch.setattr(bt, "SessionLocal", sessionmaker(autocommit=False, autoflush=False, bind=engine))

        m = bt.BatchTaskManager()
        tid = m.create_task(["A", "B"], "rad01")
        m.start_task(tid)
        m.update_progress(tid, "A", {"caseId": "A", "ok": True})
        m.update_progress(tid, "B", {"caseId": "B", "ok": True})
        m.finish_task(tid, status="completed")

        t = m.get_task(tid)
        assert t.status == "completed"
        assert t.done == 2
        assert len(t.results) == 2

    def test_cleanup_stale_marks_interrupted(self, tmp_path, monkeypatch):
        """进程重启后 running 任务被标记 failed，避免前端无限轮询"""
        import database
        from sqlalchemy import create_engine, event
        from sqlalchemy.orm import sessionmaker
        import services.batch_task as bt

        engine = create_engine(f"sqlite:///{tmp_path}/t3.db", connect_args={"check_same_thread": False})
        event.listen(engine, "connect", database._sqlite_pragmas)
        database.Base.metadata.create_all(bind=engine)
        monkeypatch.setattr(bt, "SessionLocal", sessionmaker(autocommit=False, autoflush=False, bind=engine))

        m = bt.BatchTaskManager()
        tid = m.create_task(["A"], "rad01")
        m.start_task(tid)
        cleaned = m.cleanup_stale()
        assert cleaned == 1
        assert m.get_task(tid).status == "failed"

    def test_list_active_counts_queue_depth(self, tmp_path, monkeypatch):
        """
        list_active 表示"队列深度"：等待中 + 执行中，不含已结束任务。
        语义对齐 main.py 健康检查的 batchQueue.activeTasks 指标。
        """
        import database
        from sqlalchemy import create_engine, event
        from sqlalchemy.orm import sessionmaker
        import services.batch_task as bt

        engine = create_engine(f"sqlite:///{tmp_path}/t4.db", connect_args={"check_same_thread": False})
        event.listen(engine, "connect", database._sqlite_pragmas)
        database.Base.metadata.create_all(bind=engine)
        monkeypatch.setattr(bt, "SessionLocal", sessionmaker(autocommit=False, autoflush=False, bind=engine))

        m = bt.BatchTaskManager()
        t1 = m.create_task(["A"], "rad01")
        t2 = m.create_task(["B"], "rad01")
        t3 = m.create_task(["C"], "rad01")
        m.start_task(t1)              # running
        m.finish_task(t3, "completed")  # 已结束，不计入

        active_ids = {t.task_id for t in m.list_active()}
        assert active_ids == {t1, t2}

    def test_to_dict_contract_unchanged(self, tmp_path, monkeypatch):
        """对外响应结构必须与旧版一致，前端零改动"""
        import database
        from sqlalchemy import create_engine, event
        from sqlalchemy.orm import sessionmaker
        import services.batch_task as bt

        engine = create_engine(f"sqlite:///{tmp_path}/t5.db", connect_args={"check_same_thread": False})
        event.listen(engine, "connect", database._sqlite_pragmas)
        database.Base.metadata.create_all(bind=engine)
        monkeypatch.setattr(bt, "SessionLocal", sessionmaker(autocommit=False, autoflush=False, bind=engine))

        m = bt.BatchTaskManager()
        tid = m.create_task(["A"], "rad01")
        d = m.to_dict(m.get_task(tid))
        for key in ("taskId", "status", "total", "done", "currentCaseId",
                    "results", "error", "createdAt", "startedAt", "finishedAt"):
            assert key in d, f"响应缺少字段 {key}"
