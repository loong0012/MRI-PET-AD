"""
批量分析任务 ORM 模型
------------------------------------------------------------------
把原先 `services.batch_task` 的内存字典队列落库，解决两个问题：
1. 多 worker 部署时各进程内存不共享（main.py 曾为此告警）；
2. 进程重启后任务丢失，前端轮询会无限等待。

表字段与 BatchTask dataclass 一一对应，JSON 列存 case_ids / results。
"""
from sqlalchemy import Column, String, Integer, Float, Text

from database import Base


class BatchTaskRecord(Base):
    __tablename__ = "batch_task_records"

    task_id = Column(String(64), primary_key=True)
    case_ids_json = Column(Text, default="[]")
    operator = Column(String(50), default="")
    status = Column(String(20), default="pending")  # pending / running / completed / failed
    total = Column(Integer, default=0)
    done = Column(Integer, default=0)
    current_case_id = Column(String(30), default="")
    results_json = Column(Text, default="[]")
    error = Column(Text, default="")
    created_at = Column(Float, default=0.0)
    started_at = Column(Float, default=0.0)
    finished_at = Column(Float, default=0.0)
