"""
通用工具函数
- mulberry32 PRNG（与前端 Mock 一致，保证种子数据稳定）
- 日期格式化
- 风险评分→四级风险等级映射
"""
from datetime import datetime


def mulberry32(seed: int):
    """简易种子随机数（mulberry32）：保证种子数据在每次初始化间保持稳定"""
    a = seed & 0xFFFFFFFF
    while True:
        a = (a + 0x6D2B79F5) & 0xFFFFFFFF
        t = (a ^ (a >> 15)) * (1 | a)
        t = (t + (t ^ (t >> 7)) * (61 | t)) ^ t
        yield ((t ^ (t >> 14)) & 0xFFFFFFFF) / 4294967296


def str_seed(s: str) -> int:
    """由字符串生成稳定数字种子"""
    h = 2166136261
    for c in s:
        h ^= ord(c)
        h = (h * 16777619) & 0xFFFFFFFF
    return h


def format_date_time(d: datetime = None) -> str:
    """日期格式化：Date → 'YYYY-MM-DD HH:mm:ss'"""
    d = d or datetime.now()
    return d.strftime("%Y-%m-%d %H:%M:%S")


def format_date(d: datetime = None) -> str:
    """日期格式化：Date → 'YYYY-MM-DD'"""
    d = d or datetime.now()
    return d.strftime("%Y-%m-%d")


def now_str() -> str:
    return format_date_time()


def next_seq_id(db, model, prefix: str, start: int = 51001) -> str:
    """
    生成表主键 id（如 CL51021 / AD260049）。
    基于现有最大数字后缀 + 1，而非行计数 count()：
    count() 方案在删除记录后会导致新 id 与残留高 id 冲突（主键 IntegrityError）。
    """
    max_num = start - 1
    for (rid,) in db.query(model.id).all():
        if isinstance(rid, str) and rid.startswith(prefix):
            tail = rid[len(prefix):]
            if tail.isdigit():
                max_num = max(max_num, int(tail))
    return f"{prefix}{max_num + 1}"


def score_to_risk_level(score: float, threshold: float = 0.8) -> str:
    """
    风险评分 → 四级风险等级
    score: 0-100；threshold: 0-1，折算为评分分界
    """
    early_cut = threshold * 100 * 0.75  # AD 早期分界
    if score < 35:
        return "low"
    if score < early_cut:
        return "mci"
    if score < threshold * 100:
        return "ad-early"
    return "ad-late"


# CSV 公式注入防护：以这些字符开头的单元格会被 Excel 当作公式执行
# （如 =CMD / =HYPERLINK），导出含用户可控字段（姓名、备注等）时必须转义
_CSV_INJECT_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def csv_sanitize(cell) -> str:
    """将任意值转为安全的 CSV 单元格字符串：危险前缀字符前补单引号"""
    s = "" if cell is None else str(cell)
    if s.startswith(_CSV_INJECT_PREFIXES):
        return "'" + s
    return s
