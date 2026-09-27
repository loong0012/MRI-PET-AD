"""
AI 分析路由
- POST /analysis/{caseId}/run：启动 AI 推理（同步返回）
- GET  /analysis/{caseId}/run-stream：启动 AI 推理（SSE 流式推送进度）
- GET  /analysis/{caseId}：获取分析结果
- POST /analysis/{caseId}/save：保存分析结果
"""
import json
import logging
import threading
from collections import OrderedDict

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from database import get_db, SessionLocal
from schemas.common import ok, fail
from models.case import CaseRecord as CaseModel
from models.analysis import AnalysisRecord, AnalysisVersionRecord
from models.model_config import ModelConfigRecord
from models.log import InferenceLog, CaseLog
from services.auth import get_current_user_qs
from services.inference import build_analysis, enrich_legacy_analysis
from services.data_init import case_record_to_dict
from services.utils import now_str, next_seq_id
from services.batch_task import batch_task_manager
from services.rate_limit import rate_limit

logger = logging.getLogger(__name__)

# AI 分析全部端点要求登录（操作人统一从 JWT 解析，审计日志不再使用硬编码账号）
router = APIRouter(
    prefix="/analysis",
    tags=["AI 分析"],
    dependencies=[Depends(get_current_user_qs)],
)


def _save_version(db: Session, case_id: str, result: dict, operator: str = "rad01") -> AnalysisVersionRecord:
    """将本次分析结果追加为历史版本（版本号病例内自增，默认待审核）"""
    last = (
        db.query(AnalysisVersionRecord)
        .filter(AnalysisVersionRecord.case_id == case_id)
        .order_by(AnalysisVersionRecord.version.desc())
        .first()
    )
    ver = (last.version + 1) if last else 1
    rec = AnalysisVersionRecord(
        case_id=case_id,
        version=ver,
        result_json=json.dumps(result, ensure_ascii=False),
        model_version=str(result.get("modelVersion", "")),
        fusion_strategy=str(result.get("fusionStrategy", "")),
        risk_level=str(result.get("riskLevel", "") or ""),
        risk_score=float(result.get("riskScore", 0) or 0),
        source=str(result.get("inferenceSource", "simulated")),
        operator=operator,
        created_at=now_str(),
        review_status="pending",
    )
    db.add(rec)
    return rec


def _version_to_dict(rec: AnalysisVersionRecord, with_result: bool = True) -> dict:
    """版本记录序列化"""
    data = {
        "id": rec.id,
        "caseId": rec.case_id,
        "version": rec.version,
        "modelVersion": rec.model_version,
        "fusionStrategy": rec.fusion_strategy,
        "riskLevel": rec.risk_level,
        "riskScore": rec.risk_score,
        "source": rec.source,
        "operator": rec.operator,
        "createdAt": rec.created_at,
        "reviewStatus": rec.review_status,
        "reviewer": rec.reviewer,
        "reviewComment": rec.review_comment,
        "reviewedAt": rec.reviewed_at,
    }
    if with_result:
        try:
            data["result"] = json.loads(rec.result_json)
        except Exception as e:
            logger.warning("分析版本 result_json 解析失败（version_id=%s case=%s），返回空结果：%s",
                           rec.id, rec.case_id, e)
            data["result"] = {}
    return data


def _get_model_config(db: Session) -> dict:
    """获取当前模型配置"""
    cfg = db.query(ModelConfigRecord).first()
    if not cfg:
        return {
            "strategy": "feature",
            "mriWeight": 0.55,
            "riskThreshold": 0.8,
            "roiRegions": ["海马", "颞叶皮层", "内嗅皮层", "后扣带回"],
            "snapshotVersion": "TransMF-15ens-v4",
        }
    return {
        "strategy": cfg.strategy,
        "mriWeight": cfg.mri_weight,
        "riskThreshold": cfg.risk_threshold,
        "roiRegions": json.loads(cfg.roi_regions),
        "snapshotVersion": cfg.snapshot_version,
    }


# 推理 per-case 互斥锁：防止同步 run / SSE run-stream / 批量并发跑同一病例
# 导致 AnalysisVersion 主键冲突与 CaseRecord 状态互相覆盖
# 有界 LRU：累计病例数持续增长时淘汰最久未用且当前未被占用的锁，避免字典无界膨胀
_case_locks: "OrderedDict[str, threading.Lock]" = OrderedDict()
_case_locks_guard = threading.Lock()
_CASE_LOCK_MAX = 4096


def _get_case_lock(case_id: str) -> threading.Lock:
    """获取（或创建）case_id 专属的推理互斥锁（LRU 访问置新）"""
    with _case_locks_guard:
        lock = _case_locks.get(case_id)
        if lock is None:
            lock = threading.Lock()
            _case_locks[case_id] = lock
            if len(_case_locks) > _CASE_LOCK_MAX:
                # 淘汰一个最久未访问且此刻未被占用的旧锁（跳过仍在推理中的）
                for old_id, old_lock in list(_case_locks.items()):
                    if old_id == case_id:
                        continue
                    if not old_lock.locked():
                        del _case_locks[old_id]
                        break
        else:
            _case_locks.move_to_end(case_id)
        return lock


def _execute_inference(db: Session, case_id: str, on_progress=None, operator: str = "rad01"):
    """
    推理核心逻辑（同步 + SSE 共用）
    - on_progress(percent:int, stage:str)：进度回调
    返回 (result_dict, error_message)
    """
    case = db.query(CaseModel).filter(CaseModel.id == case_id, CaseModel.is_deleted.is_(False)).first()
    if not case:
        return None, "病例不存在"

    # per-case 串行化推理：同一病例的并发请求在此排队，避免版本主键冲突与状态覆盖
    case_lock = _get_case_lock(case_id)
    with case_lock:
        case_data = case_record_to_dict(case)
        model_config = _get_model_config(db)

        # 标记推理中：前端可据此显示"推理中"状态，并作为并发判据
        case.diag_status = "AI分析中"
        db.commit()
        try:
            result = build_analysis(case_data, model_config, on_progress=on_progress)
        except Exception:
            # 推理异常：回滚状态，避免病例卡在"AI分析中"
            case.diag_status = "待AI分析"
            db.commit()
            raise

        # 保存分析结果
        existing = db.query(AnalysisRecord).filter(AnalysisRecord.case_id == case_id).first()
        if existing:
            existing.result_json = json.dumps(result, ensure_ascii=False)
        else:
            db.add(AnalysisRecord(case_id=case_id, result_json=json.dumps(result, ensure_ascii=False)))

        # 追加历史版本（待审核）
        _save_version(db, case_id, result, operator)

        # 更新病例状态
        case.risk_level = result["riskLevel"]
        case.risk_score = result["riskScore"]
        case.status = "completed"
        if case.diag_status in ("待AI分析", "AI分析中"):
            case.diag_status = "AI已分析"

        source = result.get("inferenceSource", "simulated")
        # 推理日志（使用 next_seq_id 生成稳健主键，避免时间戳冲突）
        db.add(InferenceLog(
            id=next_seq_id(db, InferenceLog, "INF", start=51001),
            case_id=case_id,
            patient_name=case_data["patient"]["name"],
            model_version=result["modelVersion"],
            strategy=result["fusionStrategy"],
            duration_sec=result["inferenceTime"],
            risk_score=result["riskScore"],
            status="成功",
            source=source,
            operator=operator,
            time=now_str(),
        ))

        # 病例操作日志
        db.add(CaseLog(
            id=next_seq_id(db, CaseLog, "CL"),
            case_id=case_id,
            patient_name=case_data["patient"]["name"],
            action="启动 AI 分析",
            operator=operator,
            time=now_str(),
            detail=f"推理完成（{'真实模型' if source == 'real' else '模拟'}），风险评分 {result['riskScore']}",
        ))

        db.commit()
        return result, None


@router.post("/{case_id}/run")
def run_inference(
    case_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_qs),
):
    """启动多模态 AI 融合分析（同步返回）"""
    result, err = _execute_inference(db, case_id, operator=current_user["username"])
    if err:
        return fail(err, 404)
    return ok(result)


@router.get("/{case_id}/run-stream")
def run_inference_stream(
    case_id: str,
    current_user: dict = Depends(get_current_user_qs),
):
    """
    启动多模态 AI 融合分析（SSE 流式推送进度）
    鉴权：EventSource 无法自定义请求头，前端通过 ?token=<JWT> 传递（get_current_user_qs 支持）。
    事件类型：
      data: {"type":"progress","progress":42,"stage":"..."}
      data: {"type":"complete","result":{...}}
      data: {"type":"error","message":"..."}
    推理在后台线程执行，进度事件经队列实时推送给前端。
    """
    import queue
    import threading

    # 操作人在主线程从 JWT 解析后传入后台 worker（线程内不再读取请求上下文）
    operator = current_user["username"]

    def event_stream():
        q: "queue.Queue" = queue.Queue()

        def worker():
            db = None
            try:
                db = SessionLocal()
                def on_progress(percent: int, stage: str):
                    q.put({"type": "progress", "progress": percent, "stage": stage})

                q.put({"type": "progress", "progress": 2, "stage": "正在初始化推理引擎…"})
                result, err = _execute_inference(db, case_id, on_progress=on_progress, operator=operator)
                if err:
                    q.put({"type": "error", "message": err})
                else:
                    # build_analysis 内部已发 100% 进度，此处不再重复，仅发 complete 携带结果
                    q.put({"type": "complete", "result": result})
            except Exception as e:
                # 异常详情（内部路径 / SQL / 堆栈）只落服务端日志，SSE 绕过了全局异常处理器，
                # 不能把 str(e) 原文回传前端造成实现细节泄漏
                logger.exception("SSE 后台推理异常（case_id=%s）", case_id)
                q.put({"type": "error", "message": "推理失败，请稍后重试或联系管理员"})
            finally:
                # SessionLocal 失败时 db 仍为 None；哨兵必须发，避免 event_stream 的 q.get() 永久阻塞
                if db is not None:
                    db.close()
                q.put(None)  # 结束哨兵

        t = threading.Thread(target=worker, daemon=True)
        t.start()

        while True:
            evt = q.get()
            if evt is None:
                break
            yield _sse(evt)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _sse(data: dict) -> str:
    """格式化 SSE 消息"""
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.get("/{case_id}")
def get_analysis(case_id: str, db: Session = Depends(get_db)):
    """获取 AI 分析结果

    旧记录自动补齐 2026 版指南字段（biologicalStage / ariaRisk / imageQuality /
    petPatterns / petTracer）：第 23/24 轮指南升级前的旧 result_json 缺少这些字段，
    前端 v-if 条件渲染导致 4 张新卡片不显示。检测到缺失时调用
    enrich_legacy_analysis 仅补齐 5 个新字段并写回数据库，避免每次查询都重算，
    其余字段（用户审核状态、人工标注等）一律保留。
    """
    rec = db.query(AnalysisRecord).filter(AnalysisRecord.case_id == case_id).first()
    if not rec:
        return fail("尚未生成 AI 分析结果", 404)
    result = json.loads(rec.result_json)
    if not result.get("biologicalStage"):
        case = db.query(CaseModel).filter(
            CaseModel.id == case_id, CaseModel.is_deleted.is_(False)
        ).first()
        if case:
            case_data = case_record_to_dict(case)
            model_config = _get_model_config(db)
            result = enrich_legacy_analysis(result, case_data, model_config)
            rec.result_json = json.dumps(result, ensure_ascii=False)
            db.commit()
    return ok(result)


@router.post("/{case_id}/save")
def save_analysis(
    case_id: str,
    result: dict,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_qs),
):
    """保存 AI 分析结果"""
    operator = current_user["username"]
    existing = db.query(AnalysisRecord).filter(AnalysisRecord.case_id == case_id).first()
    if existing:
        existing.result_json = json.dumps(result, ensure_ascii=False)
    else:
        db.add(AnalysisRecord(case_id=case_id, result_json=json.dumps(result, ensure_ascii=False)))

    # 手动保存同样追加历史版本
    _save_version(db, case_id, result, operator)

    case = db.query(CaseModel).filter(CaseModel.id == case_id, CaseModel.is_deleted.is_(False)).first()
    if case:
        case.risk_level = result.get("riskLevel")
        case.risk_score = result.get("riskScore")
        case.status = "completed"
        if case.diag_status in ("待AI分析", "AI分析中"):
            case.diag_status = "AI已分析"

    db.add(CaseLog(
        id=next_seq_id(db, CaseLog, "CL"),
        case_id=case_id,
        patient_name=json.loads(case.patient_json).get("name", "") if case else "",
        action="保存 AI 分析结果",
        operator=operator,
        time=now_str(),
        detail=f"保存风险评分 {result.get('riskScore')} 的分析结果",
    ))
    db.commit()
    return ok(None, "保存成功")


def _batch_worker(task_id: str, case_ids: list[str], operator: str):
    """批量分析后台线程：逐个病例执行推理，更新任务进度"""
    task = batch_task_manager.get_task(task_id)
    if not task:
        return
    batch_task_manager.start_task(task_id)
    for cid in case_ids:
        # 后台线程需独立 DB 会话
        thread_db = SessionLocal()
        try:
            result, err = _execute_inference(thread_db, cid, on_progress=None, operator=operator)
            if err:
                batch_task_manager.update_progress(task_id, cid, {
                    "caseId": cid, "status": "failed", "error": err
                })
            else:
                batch_task_manager.update_progress(task_id, cid, {
                    "caseId": cid, "status": "success",
                    "riskScore": result.get("riskScore"),
                    "riskLevel": result.get("riskLevel"),
                })
        except Exception as e:
            thread_db.rollback()
            batch_task_manager.update_progress(task_id, cid, {
                "caseId": cid, "status": "failed", "error": str(e)
            })
        finally:
            thread_db.close()
    batch_task_manager.finish_task(task_id, status="completed")


@router.post("/batch")
def batch_analysis(
    payload: dict,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_qs),
    # 批量分析 CPU 密集（每病例 75 模型前向），每 IP 每分钟最多 5 次防资源耗尽
    _rl: None = Depends(rate_limit("analysis_batch", 5, 60)),
):
    """
    批量 AI 分析（异步任务队列）：立即返回 taskId，后台线程逐个执行推理。
    body: { "caseIds": ["AD260001", "AD260002", ...] }
    返回：{ taskId, total }，前端轮询 GET /analysis/batch/{taskId}/status 获取进度
    """
    case_ids = payload.get("caseIds") or []
    operator = current_user["username"]
    if not case_ids:
        return fail("未提供病例 ID 列表", 400)
    # 每例一次真实 torch 推理且每请求独占线程：批量上限 200，防 CPU/显存/线程耗尽
    BATCH_ANALYSIS_MAX = 200
    if len(case_ids) > BATCH_ANALYSIS_MAX:
        return fail(f"单次最多批量分析 {BATCH_ANALYSIS_MAX} 例，请分批提交", 400)

    task_id = batch_task_manager.create_task(case_ids, operator)
    # 后台线程执行批量推理
    t = threading.Thread(target=_batch_worker, args=(task_id, case_ids, operator), daemon=True)
    t.start()
    return ok({"taskId": task_id, "total": len(case_ids)}, "批量分析任务已提交")


@router.get("/batch/tasks")
def list_batch_tasks(current_user: dict = Depends(get_current_user_qs)):
    """获取当前用户的批量分析任务列表（最近 20 条）"""
    operator = current_user.get("username", "")
    tasks = batch_task_manager.list_tasks(operator=operator)
    return ok([batch_task_manager.to_dict(t) for t in tasks])


@router.get("/batch/{task_id}/status")
def get_batch_task_status(task_id: str, current_user: dict = Depends(get_current_user_qs)):
    """获取批量分析任务状态与进度（仅任务发起人本人或管理员可查，防水平越权枚举）"""
    task = batch_task_manager.get_task(task_id)
    if not task:
        return fail("任务不存在", 404)
    if task.operator != current_user.get("username") and current_user.get("role") != "admin":
        return fail("无权查看该任务", 403)
    return ok(batch_task_manager.to_dict(task))


@router.get("/{case_id}/versions")
def list_versions(case_id: str, db: Session = Depends(get_db)):
    """获取病例的全部分析历史版本（含完整结果，用于版本对比）"""
    recs = (
        db.query(AnalysisVersionRecord)
        .filter(AnalysisVersionRecord.case_id == case_id)
        .order_by(AnalysisVersionRecord.version.desc())
        .all()
    )
    return ok([_version_to_dict(r) for r in recs])


@router.get("/version/{version_id}")
def get_version(version_id: int, db: Session = Depends(get_db)):
    """获取单个分析版本详情"""
    rec = db.query(AnalysisVersionRecord).filter(AnalysisVersionRecord.id == version_id).first()
    if not rec:
        return fail("版本不存在", 404)
    return ok(_version_to_dict(rec))


@router.post("/version/{version_id}/review")
def review_version(
    version_id: int,
    payload: dict,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_qs),
):
    """
    协作审核：approve（通过）/ reject（驳回）
    body: { "action": "approve" | "reject", "comment": "..." }
    审核人取自 JWT（忽略请求体中可能伪造的 reviewer 字段）。
    """
    action = (payload.get("action") or "").lower()
    if action not in ("approve", "reject"):
        return fail("action 必须为 approve 或 reject", 400)

    rec = db.query(AnalysisVersionRecord).filter(AnalysisVersionRecord.id == version_id).first()
    if not rec:
        return fail("版本不存在", 404)

    reviewer = current_user["username"]
    comment = payload.get("comment") or ""
    status = "approved" if action == "approve" else "rejected"

    rec.review_status = status
    rec.reviewer = reviewer
    rec.review_comment = comment
    rec.reviewed_at = now_str()

    # 同步病例诊断状态
    case = db.query(CaseModel).filter(CaseModel.id == rec.case_id, CaseModel.is_deleted.is_(False)).first()
    patient_name = ""
    if case:
        patient_name = json.loads(case.patient_json).get("name", "")
        case.diag_status = "医生已审核" if action == "approve" else "审核驳回"

    db.add(CaseLog(
        id=next_seq_id(db, CaseLog, "CL"),
        case_id=rec.case_id,
        patient_name=patient_name,
        action="审核通过" if action == "approve" else "审核驳回",
        operator=reviewer,
        time=now_str(),
        detail=f"分析版本 v{rec.version} {('通过' if action == 'approve' else '驳回')}：{comment or '无备注'}",
    ))
    db.commit()
    return ok(_version_to_dict(rec, with_result=False), "审核已提交")
