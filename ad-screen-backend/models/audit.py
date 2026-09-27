"""
合规审计事件 ORM 模型
------------------------------------------------------------------
设计原则（对齐 HIPAA §164.312(b) / GDPR 第30条 / 等保三级）：

1. **append-only**：只允许 INSERT。数据库层由触发器强制（见 database.py
   `_install_audit_triggers`），绕过应用层直接改库也会被拒绝。
2. **哈希链防篡改**：每条记录携带 `prev_hash` 与上一条的 `hash`，
   形成链式结构。修改任意一条历史记录，其后所有记录的哈希校验都会断裂，
   这比"靠权限控制防改"更强——它是数学上的证明，而非管理上的约定。
3. **字段完备**：对齐行业共识的审计字段集（用户/时间/患者/动作/资源/
   结果/IP/追踪ID/模型版本），能独立回答合规审查的核心问题。

命名说明：模型类名用 `AuditEvent` 而非 `Audit`，避免与 audit_board（统计看板）
概念混淆——后者是聚合视图，本表是事实记录。
"""
from sqlalchemy import Column, String, Text, Float, Index, Integer

from database import Base


class AuditEvent(Base):
    """审计事件（追加写，不可修改/删除）"""

    __tablename__ = "audit_events"

    # 主键：AU + 递增序号（与项目既有 next_seq_id 约定一致）
    id = Column(String(30), primary_key=True)

    # ---------- 时间 ----------
    # ts 为人类可读串（与既有日志表格式一致，便于 DBA 直接查）；
    # ts_epoch 为数值时间戳，供范围查询与保留策略高效下推
    ts = Column(String(30), nullable=False, index=True)
    ts_epoch = Column(Float, nullable=False, default=0.0, index=True)

    # ---------- 操作者 ----------
    actor = Column(String(50), default="", index=True)          # 用户名，空=匿名/未认证
    actor_role = Column(String(50), default="")                 # 角色快照（角色会变，审计需留当时值）
    actor_ip = Column(String(64), default="")                   # 源 IP

    # ---------- 动作与资源 ----------
    # action 取值见 services/audit_policy.py：read/write/update/delete/
    # export/login/logout/config/infer/override
    action = Column(String(20), default="", index=True)
    resource_type = Column(String(30), default="", index=True)
    resource_id = Column(String(80), default="", index=True)

    # 患者引用：合规调查的核心检索维度（"谁看过这个病人"）
    # 存病例号而非姓名——姓名属 PHI，审计表本身不应成为 PHI 泄漏源
    patient_ref = Column(String(80), default="", index=True)

    # ---------- 结果 ----------
    # success / failure / denied（denied=鉴权拒绝，是入侵检测的关键信号）
    outcome = Column(String(10), default="success", index=True)
    # info / warn / critical（critical 用于导出、删除、权限变更等高风险动作）
    severity = Column(String(10), default="info")

    # ---------- 上下文 ----------
    detail = Column(Text, default="")            # JSON 串，禁止写入 PHI 明文
    request_id = Column(String(40), default="")  # 关联 access log 的 trace_id，打通全链路
    model_version = Column(String(50), default="")  # 推理类事件必填，供 SaMD 追溯

    # ---------- 防篡改 ----------
    prev_hash = Column(String(64), default="")
    hash = Column(String(64), default="", index=True)


# 复合索引：合规查询最常见的模式是"某资源/某患者 + 时间倒序"
Index("ix_audit_resource_time", AuditEvent.resource_type, AuditEvent.ts_epoch)
Index("ix_audit_actor_time", AuditEvent.actor, AuditEvent.ts_epoch)


class AuditChainHead(Base):
    """
    哈希链头（单行表，固定 id=1）

    为什么需要它：
    append-only 触发器禁止对 audit_events 做任何 UPDATE，所以哈希必须在
    INSERT 之前就算好——不能"先插入再回填"。而计算哈希需要知道上一条的哈希，
    这就需要一处可写的地方保存链头。本表承担这个角色。

    附加收益：seq 字段让主键生成从 O(n)（扫全表求 max）降为 O(1)。
    审计表会持续增长到百万级，逐次扫全表取最大 id 是不可接受的。
    """

    __tablename__ = "audit_chain_head"

    id = Column(Integer, primary_key=True)  # 固定为 1
    seq = Column(Integer, default=0)                 # 已分配的最大序号
    last_id = Column(String(30), default="")         # 链尾记录 id
    last_hash = Column(String(64), default="")       # 链尾哈希
    updated_epoch = Column(Float, default=0.0)
