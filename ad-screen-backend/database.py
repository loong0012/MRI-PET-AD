"""
数据库初始化与会话管理
- SQLAlchemy 2.0 同步引擎 + SQLite
- 提供 get_db 依赖注入

SQLite 并发强化（医疗系统必需）：
- WAL：读写不互斥，批量推理写库期间 Web 读请求不被阻塞
- busy_timeout：争锁时等待而非立即抛 database is locked
- foreign_keys：SQLite 默认关闭外键，显式开启保证引用完整性
"""
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, declarative_base

from config import DB_URL, SQLITE_BUSY_TIMEOUT_MS, SQLITE_SYNCHRONOUS

engine = create_engine(
    DB_URL,
    connect_args={"check_same_thread": False},
    echo=False,
)


@event.listens_for(engine, "connect")
def _sqlite_pragmas(dbapi_conn, _connection_record):
    """
    每个 SQLite 连接建立时下发 PRAGMA。
    SQLite 的 PRAGMA 是连接级而非库级，必须在每次 connect 时设置。
    """
    cur = dbapi_conn.cursor()
    try:
        # 读写并发：WAL 让读事务不被写事务阻塞（默认 delete 模式下会互斥）
        cur.execute("PRAGMA journal_mode=WAL")
        # 争锁等待：默认 0（立即报 database is locked），设为 5s 大幅降低并发报错
        cur.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
        # 引用完整性：SQLite 默认 OFF，级联删除/外键约束需显式开启
        cur.execute("PRAGMA foreign_keys=ON")
        # 同步策略：NORMAL 在 WAL 下兼顾性能与崩溃安全（FULL 每次提交都 fsync）
        cur.execute(f"PRAGMA synchronous={SQLITE_SYNCHRONOUS}")
    finally:
        cur.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    """FastAPI 依赖注入：每请求获取独立数据库会话，请求结束自动关闭"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """建表 + 增量列迁移（SQLite create_all 不自动加列，需手动补齐新字段）"""
    from models import user, case, analysis, model_config, log, favorite, login_log, warning, report_template, annotation, case_comment, knowledge, review, longitudinal, batch_task  # noqa: F401
    import models.audit  # noqa: F401  （合规审计事件表）

    Base.metadata.create_all(bind=engine)
    _migrate_columns()
    _migrate_indexes()
    _install_audit_guard()


def _install_audit_guard():
    """
    为 audit_events 安装 append-only 触发器。

    应用层"不提供删除接口"只是约定，触发器把它变成约束：
    即使绕过应用直接连库执行 UPDATE/DELETE，也会被 SQLite 拒绝。
    """
    try:
        from services.audit_service import install_append_only_guard

        install_append_only_guard(engine)
    except Exception as exc:  # 触发器安装失败不应阻止启动，但必须告警
        print(f"[数据库初始化][警告] 审计表 append-only 触发器安装失败：{exc}")


def _migrate_columns():
    """
    为已存在的表补齐新增列（幂等：列已存在则跳过）。
    覆盖个人中心所需的用户扩展字段：email / title / avatar / signature。
    """
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    new_cols = {
        "users": [
            ("email", "VARCHAR(100) DEFAULT ''"),
            ("title", "VARCHAR(50) DEFAULT ''"),
            ("avatar", "TEXT DEFAULT ''"),
            ("signature", "TEXT DEFAULT ''"),
            ("token_version", "INTEGER NOT NULL DEFAULT 0"),
        ],
        "case_records": [
            ("is_deleted", "BOOLEAN DEFAULT 0"),
            ("cohort", "VARCHAR(60) DEFAULT 'UNKNOWN'"),
            ("scanner_info", "VARCHAR(200) DEFAULT ''"),
            ("dicom_meta", "TEXT DEFAULT '{}'"),
            ("exam_indication", "VARCHAR(30) DEFAULT 'diagnosis'"),
            ("pet_tracer", "VARCHAR(20) DEFAULT 'fdg'"),
            ("contraindications", "TEXT DEFAULT '{}'"),
            ("special_population", "VARCHAR(30) DEFAULT 'normal'"),
        ],
    }
    with engine.begin() as conn:
        for table, cols in new_cols.items():
            existing = {c["name"] for c in inspector.get_columns(table)} if table in inspector.get_table_names() else set()
            for col_name, col_def in cols:
                if col_name not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_def}"))


def _migrate_indexes():
    """
    为核心表补齐高频查询索引（幂等：CREATE INDEX IF NOT EXISTS）。

    case_records 是系统核心表，但模型定义除主键外未声明索引，
    列表页/仪表盘/导出等查询随数据量增长会全表扫描。此处补齐：
    - (is_deleted, create_time)：列表查询必带软删除过滤 + 时间倒序，复合索引最常用
    - status / risk_level / cohort / modality：筛选与分组统计高频维度
    """
    from sqlalchemy import text

    indexes = [
        ("idx_case_status", "case_records", "status"),
        ("idx_case_risk_level", "case_records", "risk_level"),
        ("idx_case_cohort", "case_records", "cohort"),
        ("idx_case_modality", "case_records", "modality"),
        # 复合索引：WHERE is_deleted=0 ORDER BY create_time DESC（列表查询主力路径）
        ("idx_case_deleted_time", "case_records", "is_deleted, create_time"),
    ]
    with engine.begin() as conn:
        for idx_name, table, columns in indexes:
            conn.execute(text(
                f"CREATE INDEX IF NOT EXISTS {idx_name} ON {table} ({columns})"
            ))

