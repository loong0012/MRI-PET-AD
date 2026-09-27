"""
系统运行审计看板（超级管理员）
- GET /audit-board/overview?days=30：登录趋势 / 活跃用户 / 病例操作分布 /
  系统模块使用 / 失败登录时段 / 关键汇总指标

数据来源：login_records + case_logs + system_logs + inference_logs，纯只读聚合。
"""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, case as sa_case

from database import get_db
from schemas.common import ok
from models.login_log import LoginRecord
from models.log import CaseLog, SystemLog, InferenceLog
from services.auth import require_role

router = APIRouter(
    prefix="/audit-board",
    tags=["运行审计看板"],
    dependencies=[Depends(require_role("admin"))],
)


@router.get("/overview")
def audit_overview(days: int = Query(default=30, ge=7, le=180), db=Depends(get_db)):
    now = datetime.now()
    since = now - timedelta(days=days - 1)
    since_day = since.date()

    # ---------- 登录趋势（SQL 按日分组下推，不加载全表行）----------
    # login_at 是 DateTime 列；用 date(login_at) 分组、按 success 分别计数
    since_dt = datetime(since_day.year, since_day.month, since_day.day)
    day_col = func.date(LoginRecord.login_at)
    login_day_rows = (
        db.query(
            day_col.label("d"),
            func.sum(sa_case((LoginRecord.success == 1, 1), else_=0)).label("ok"),
            func.sum(sa_case((LoginRecord.success == 1, 0), else_=1)).label("fail"),
        )
        .filter(LoginRecord.login_at >= since_dt)
        .group_by(day_col)
        .all()
    )
    day_map = {r.d: (int(r.ok or 0), int(r.fail or 0)) for r in login_day_rows}

    trend_map: dict[str, dict] = {}
    login_ok = login_fail = 0
    for i in range(days):
        d_full = (since + timedelta(days=i)).date()
        d = d_full.strftime("%m-%d")
        ok_cnt, fail_cnt = day_map.get(str(d_full), (0, 0))
        trend_map[d] = {"date": d, "success": ok_cnt, "fail": fail_cnt}
        login_ok += ok_cnt
        login_fail += fail_cnt

    # ---------- 活跃用户（SQL 按用户名分组下推）----------
    user_rows = (
        db.query(
            LoginRecord.username,
            func.max(LoginRecord.real_name).label("real_name"),
            func.sum(sa_case((LoginRecord.success == 1, 1), else_=0)).label("ok"),
            func.sum(sa_case((LoginRecord.success == 1, 0), else_=1)).label("fail"),
        )
        .filter(LoginRecord.login_at >= since_dt)
        .group_by(LoginRecord.username)
        .all()
    )
    user_stat = {
        r.username: {
            "username": r.username,
            "realName": r.real_name or r.username,
            "success": int(r.ok or 0),
            "fail": int(r.fail or 0),
        }
        for r in user_rows
    }

    # ---------- 失败登录时段分布（SQL 按小时分组下推）----------
    hour_col = func.strftime("%H", LoginRecord.login_at)
    hour_rows = (
        db.query(hour_col.label("h"), func.count().label("cnt"))
        .filter(LoginRecord.login_at >= since_dt, LoginRecord.success != 1)
        .group_by(hour_col)
        .all()
    )
    fail_hours = [0] * 24
    for r in hour_rows:
        if r.h is not None:
            fail_hours[int(r.h)] = int(r.cnt)

    active_users = sorted(
        user_stat.values(), key=lambda x: (x["success"] + x["fail"]), reverse=True
    )[:8]

    # ---------- 病例操作类型分布（SQL 分组下推，取 Top10）----------
    # time 为定长 "%Y-%m-%d %H:%M:%S" 字符串列，字典序即时间序，范围下推 SQL
    since_str = since_day.strftime("%Y-%m-%d") + " 00:00:00"
    case_action_rows = (
        db.query(CaseLog.action, func.count().label("cnt"))
        .filter(CaseLog.time >= since_str)
        .group_by(CaseLog.action)
        .order_by(func.count().desc())
        .limit(10)
        .all()
    )
    case_actions = [
        {"name": ((r.action or "其他操作").strip() or "其他操作"), "value": int(r.cnt)}
        for r in case_action_rows
    ]
    case_ops_total = (
        db.query(func.count()).filter(CaseLog.time >= since_str).scalar() or 0
    )

    # ---------- 系统模块使用分布（SQL 分组下推，取 Top8）----------
    module_rows = (
        db.query(SystemLog.module, func.count().label("cnt"))
        .filter(SystemLog.time >= since_str)
        .group_by(SystemLog.module)
        .order_by(func.count().desc())
        .limit(8)
        .all()
    )
    module_usage = [
        {"name": ((r.module or "其他").strip() or "其他"), "value": int(r.cnt)}
        for r in module_rows
    ]
    system_ops_total = (
        db.query(func.count()).filter(SystemLog.time >= since_str).scalar() or 0
    )

    # ---------- AI 推理统计（SQL 聚合下推：总数/失败数/平均耗时）----------
    inf_agg = (
        db.query(
            func.count().label("total"),
            func.sum(sa_case((InferenceLog.status == "成功", 0), else_=1)).label("fail"),
            func.avg(InferenceLog.duration_sec).label("avg_dur"),
        )
        .filter(InferenceLog.time >= since_str)
        .one()
    )
    inf_total = int(inf_agg.total or 0)
    inf_fail = int(inf_agg.fail or 0)
    avg_duration = round(float(inf_agg.avg_dur or 0.0), 1)

    total_login = login_ok + login_fail
    fail_rate = round(login_fail / total_login * 100, 1) if total_login else 0.0

    return ok({
        "rangeDays": days,
        "totals": {
            "loginCount": total_login,
            "loginFail": login_fail,
            "failRate": fail_rate,
            "activeUsers": len(user_stat),
            "caseOps": int(case_ops_total),
            "systemOps": int(system_ops_total),
            "inferenceCount": inf_total,
            "inferenceFail": inf_fail,
            "avgDuration": avg_duration,
        },
        "loginTrend": list(trend_map.values()),
        "activeUsers": active_users,
        "caseActions": case_actions,
        "moduleUsage": module_usage,
        "failHours": [{"hour": f"{h:02d}:00", "count": fail_hours[h]} for h in range(24)],
    })
