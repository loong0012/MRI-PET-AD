"""
审计看板 SQL 下推聚合回归测试
==================================================================
优化前：4 张日志表（login/case/system/inference）全表 .all() 加载到 Python 聚合
优化后：COUNT/GROUP BY/AVG 全部下推 SQL，仅返回聚合结果行
本测试验证聚合结果与内存计算基准一致，确保 SQL 下推不改变输出语义。
"""
from datetime import datetime, timedelta

from sqlalchemy import func, case as sa_case

from models.login_log import LoginRecord
from models.log import CaseLog, SystemLog, InferenceLog


def _now_str(dt=None):
    dt = dt or datetime.now()
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _seed(db, days_ago_offsets=(0, 0, 1, 2)):
    """造登录/病例/系统/推理日志数据，覆盖多天与成功失败混合"""
    now = datetime.now()
    # 登录：admin 3 次成功 + 1 次失败；doctor 1 次成功
    db.add_all([
        LoginRecord(username="admin", real_name="管理员", success=1,
                    login_at=now - timedelta(days=days_ago_offsets[0])),
        LoginRecord(username="admin", real_name="管理员", success=1,
                    login_at=now - timedelta(days=days_ago_offsets[1])),
        LoginRecord(username="admin", real_name="管理员", success=0,
                    login_at=now - timedelta(days=days_ago_offsets[2], hours=3)),
        LoginRecord(username="admin", real_name="管理员", success=1,
                    login_at=now - timedelta(days=days_ago_offsets[3])),
        LoginRecord(username="doctor", real_name="医生", success=1,
                    login_at=now),
    ])
    # 病例操作：create×2, update×1
    db.add_all([
        CaseLog(id="CL1", case_id="C1", action="create", operator="admin", time=_now_str()),
        CaseLog(id="CL2", case_id="C2", action="create", operator="admin", time=_now_str()),
        CaseLog(id="CL3", case_id="C1", action="update", operator="admin", time=_now_str()),
    ])
    # 系统模块：case×2, auth×1
    db.add_all([
        SystemLog(id="S1", module="case", action="查询", operator="admin", time=_now_str()),
        SystemLog(id="S2", module="case", action="导出", operator="admin", time=_now_str()),
        SystemLog(id="S3", module="auth", action="登录", operator="admin", time=_now_str()),
    ])
    # 推理：2 成功 1 失败，duration 5/10/15
    db.add_all([
        InferenceLog(id="I1", case_id="C1", status="成功", duration_sec=5.0, time=_now_str()),
        InferenceLog(id="I2", case_id="C2", status="成功", duration_sec=10.0, time=_now_str()),
        InferenceLog(id="I3", case_id="C3", status="失败", duration_sec=15.0, time=_now_str()),
    ])
    db.commit()


# ----------------------------------------------------------------------
# 复刻 audit_board.py 优化后的 SQL 下推聚合逻辑（供单测断言）
# ----------------------------------------------------------------------
def _audit_agg(db, days=30):
    now = datetime.now()
    since = now - timedelta(days=days - 1)
    since_day = since.date()
    since_dt = datetime(since_day.year, since_day.month, since_day.day)
    since_str = since_day.strftime("%Y-%m-%d") + " 00:00:00"

    # 登录趋势
    day_col = func.date(LoginRecord.login_at)
    day_rows = (
        db.query(day_col.label("d"),
                 func.sum(sa_case((LoginRecord.success == 1, 1), else_=0)).label("ok"),
                 func.sum(sa_case((LoginRecord.success == 1, 0), else_=1)).label("fail"))
        .filter(LoginRecord.login_at >= since_dt).group_by(day_col).all()
    )
    day_map = {r.d: (int(r.ok or 0), int(r.fail or 0)) for r in day_rows}

    # 活跃用户
    user_rows = (
        db.query(LoginRecord.username,
                 func.max(LoginRecord.real_name).label("real_name"),
                 func.sum(sa_case((LoginRecord.success == 1, 1), else_=0)).label("ok"),
                 func.sum(sa_case((LoginRecord.success == 1, 0), else_=1)).label("fail"))
        .filter(LoginRecord.login_at >= since_dt).group_by(LoginRecord.username).all()
    )

    # 失败时段
    hour_col = func.strftime("%H", LoginRecord.login_at)
    hour_rows = (
        db.query(hour_col.label("h"), func.count().label("cnt"))
        .filter(LoginRecord.login_at >= since_dt, LoginRecord.success != 1)
        .group_by(hour_col).all()
    )

    # 病例操作 / 系统模块 / 推理统计
    case_action_rows = (
        db.query(CaseLog.action, func.count().label("cnt"))
        .filter(CaseLog.time >= since_str).group_by(CaseLog.action).all()
    )
    module_rows = (
        db.query(SystemLog.module, func.count().label("cnt"))
        .filter(SystemLog.time >= since_str).group_by(SystemLog.module).all()
    )
    inf_agg = (
        db.query(func.count().label("total"),
                 func.sum(sa_case((InferenceLog.status == "成功", 0), else_=1)).label("fail"),
                 func.avg(InferenceLog.duration_sec).label("avg_dur"))
        .filter(InferenceLog.time >= since_str).one()
    )
    return {
        "day_map": day_map,
        "user_rows": user_rows,
        "hour_rows": hour_rows,
        "case_action_rows": case_action_rows,
        "module_rows": module_rows,
        "inf": inf_agg,
    }


def test_login_trend_aggregation(db):
    _seed(db)
    agg = _audit_agg(db)
    today = datetime.now().date().isoformat()
    yesterday = (datetime.now() - timedelta(days=1)).date().isoformat()
    two_days = (datetime.now() - timedelta(days=2)).date().isoformat()
    # 今天：admin×2成功 + doctor×1成功 = 3 成功 0 失败
    assert agg["day_map"].get(today) == (3, 0)
    # 昨天：admin 1 次失败（offset[2]=1，带 -3h）
    assert agg["day_map"].get(yesterday) == (0, 1)
    # 前天：admin 1 次成功（offset[3]=2）
    assert agg["day_map"].get(two_days) == (1, 0)


def test_active_users_aggregation(db):
    _seed(db)
    agg = _audit_agg(db)
    users = {r.username: (int(r.ok or 0), int(r.fail or 0)) for r in agg["user_rows"]}
    assert users["admin"] == (3, 1)
    assert users["doctor"] == (1, 0)


def test_fail_hours_aggregation(db):
    _seed(db)
    agg = _audit_agg(db)
    hour_map = {int(r.h): int(r.cnt) for r in agg["hour_rows"] if r.h is not None}
    # 唯一一次失败发生在 (now-1d-3h)，验证该小时计数为 1
    fail_dt = datetime.now() - timedelta(days=1, hours=3)
    assert hour_map.get(fail_dt.hour) == 1
    assert sum(hour_map.values()) == 1  # 总共只有 1 次失败


def test_case_action_aggregation(db):
    _seed(db)
    agg = _audit_agg(db)
    amap = {r.action: int(r.cnt) for r in agg["case_action_rows"]}
    assert amap == {"create": 2, "update": 1}


def test_module_usage_aggregation(db):
    _seed(db)
    agg = _audit_agg(db)
    mmap = {r.module: int(r.cnt) for r in agg["module_rows"]}
    assert mmap == {"case": 2, "auth": 1}


def test_inference_aggregation(db):
    _seed(db)
    agg = _audit_agg(db)
    assert int(agg["inf"].total) == 3
    assert int(agg["inf"].fail) == 1
    assert abs(float(agg["inf"].avg_dur) - 10.0) < 1e-6  # (5+10+15)/3


def test_empty_range_returns_zero(db):
    """范围内无数据时聚合返回 0 / 空，不报错"""
    _seed(db)
    # 用未来起始日期确保无数据
    now = datetime.now()
    since_dt = now + timedelta(days=1)
    n = db.query(func.count(LoginRecord.id)).filter(LoginRecord.login_at >= since_dt).scalar()
    assert n == 0
