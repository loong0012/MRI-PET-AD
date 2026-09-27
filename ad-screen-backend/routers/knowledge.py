"""
患者教育/科普知识库路由
------------------------------------------------------------------
面向患者及家属的阿尔茨海默病（AD）公开科普内容，无需登录即可访问。
按 6 大分类组织文章：疾病概述 / 早期症状 / 诊断方法 / 治疗方案 / 护理建议 / 预防措施。
首次访问时若表为空，自动初始化 6 篇预置科普文章。

端点：
- GET /knowledge/categories      分类列表（含各分类文章数）
- GET /knowledge/articles        文章列表（支持分类/关键词筛选）
- GET /knowledge/{article_id}    文章详情（含 Markdown 正文）
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from database import get_db, Base, engine
from schemas.common import ok, fail
from models.knowledge import KnowledgeArticle
from services.utils import format_date_time

router = APIRouter(
    prefix="/knowledge",
    tags=["科普知识库"],
)

# ==================== 分类定义 ====================

CATEGORIES = [
    {"key": "overview",    "name": "疾病概述", "icon": "Reading"},
    {"key": "symptoms",    "name": "早期症状", "icon": "Warning"},
    {"key": "diagnosis",   "name": "诊断方法", "icon": "Search"},
    {"key": "treatment",   "name": "治疗方案", "icon": "FirstAidKit"},
    {"key": "care",        "name": "护理建议", "icon": "Avatar"},
    {"key": "prevention",  "name": "预防措施", "icon": "CircleCheck"},
]

# 分类 key → 中文名 映射（用于种子数据与展示）
CATEGORY_NAME_MAP = {c["key"]: c["name"] for c in CATEGORIES}


# ==================== 预置科普文章 ====================

SEED_ARTICLES: list[dict] = [
    # ---------- 1. 疾病概述 ----------
    {
        "category": "overview",
        "title": "阿尔茨海默病是什么",
        "summary": "阿尔茨海默病（AD）是一种起病隐匿、进行性发展的神经退行性疾病，以记忆力下降和认知功能障碍为主要表现。",
        "tags": "阿尔茨海默病,AD,痴呆,神经退行性疾病",
        "icon": "Reading",
        "sort_order": 1,
        "content": """# 阿尔茨海默病是什么

**阿尔茨海默病（Alzheimer's Disease，AD）** 是一种起病隐匿、进行性发展的神经退行性疾病，是老年期痴呆最常见的类型，约占所有痴呆病例的 60%-80%。

## 核心特征

- **记忆力进行性下降**：尤其是近期记忆，早期常表现为忘记刚说过的话、刚放的东西
- **认知功能障碍**：包括语言、定向力、判断力、执行功能等多领域受损
- **日常生活能力下降**：逐渐无法独立完成穿衣、进食、如厕等基本活动
- **精神行为症状**：部分患者出现焦虑、抑郁、幻觉、妄想、激越等

## 发病机制

AD 的主要病理特征包括：
- **β-淀粉样蛋白（Aβ）沉积**：形成老年斑
- **Tau 蛋白过度磷酸化**：形成神经原纤维缠结
- **神经元与突触丢失**：尤以海马和内侧颞叶为著

## 疾病分期

临床上常分为 **临床前期、轻度认知障碍期（MCI）、痴呆期**，痴呆期又可分为轻、中、重度。疾病呈持续进展，目前尚无根治方法，但**早发现、早干预**可显著延缓病程、改善生活质量。

> 本系统的 AI 多模态分析可辅助识别 AD 早期影像学改变，建议 60 岁以上人群定期筛查。""",
    },
    # ---------- 2. 早期症状 ----------
    {
        "category": "symptoms",
        "title": "10个早期预警信号",
        "summary": "AD 早期症状易被误认为“老糊涂”而延误就医。了解 10 个典型预警信号，有助于尽早识别并及时就诊。",
        "tags": "早期症状,预警信号,记忆力,认知障碍",
        "icon": "Warning",
        "sort_order": 2,
        "content": """# AD 的 10 个早期预警信号

阿尔茨海默病早期症状往往不典型，容易被当作"正常衰老"而忽视。如果您或家人出现以下信号，建议尽早到神经内科或记忆门诊评估：

## 10 个典型信号

1. **记忆力下降影响日常生活**：反复询问同一件事，忘记重要约会或节日
2. **难以完成熟悉的任务**：做菜时忘记放盐，不会使用常用家电
3. **语言表达困难**：找不到合适的词，说话或书写出现障碍
4. **时间和地点定向障碍**：记不清日期、季节，在熟悉的地方迷路
5. **判断力下降**：对财物管理、天气变化等判断失误
6. **抽象思维困难**：无法处理数字，跟不上电视情节
7. **东西放错地方且无法回忆**：把钥匙放进冰箱等异常行为
8. **情绪或性格改变**：变得焦虑、多疑、易怒，或对以往爱好失去兴趣
9. **社交退缩**：不愿参加聚会，回避社交活动
10. **视觉空间障碍**：阅读困难，判断距离或颜色出现问题

## 何时就医

- 上述症状**持续超过 2 周**且影响生活
- 症状**逐渐加重**而非时好时坏
- 家人或同事反映"和以前不一样了"

> 出现信号不等于一定是 AD，抑郁、维生素缺乏、甲状腺疾病等也可能引起类似表现，需专业医生鉴别。""",
    },
    # ---------- 3. 诊断方法 ----------
    {
        "category": "diagnosis",
        "title": "如何确诊阿尔茨海默病",
        "summary": "AD 的诊断需要综合病史、认知评估、影像学和实验室检查。本文介绍标准诊断流程与各类检查的意义。",
        "tags": "诊断,MRI,PET,认知评估,生物标志物",
        "icon": "Search",
        "sort_order": 3,
        "content": """# 如何确诊阿尔茨海默病

AD 的诊断是一个**综合评估过程**，单一检查无法确诊。目前主流诊断框架（NIA-AA 标准）结合临床、影像和生物标志物。

## 标准诊断流程

### 1. 病史采集
- 详细了解认知症状的**起病时间、进展方式**
- 了解既往病史、用药史、家族史
- 由家属或知情者提供旁证信息

### 2. 认知与功能评估
- **MMSE / MoCA** 等认知量表评估记忆、执行、语言等领域
- **日常生活活动能力（ADL）** 量表评估功能受损程度
- 精神行为症状评估（如抑郁、幻觉）

### 3. 影像学检查
- **头颅 MRI**：评估海马萎缩、内侧颞叶萎缩（MTA 评分），排除其他病因
- **FDG-PET**：显示颞顶叶代谢减低，AD 早期后扣带回/楔前叶特征性改变
- **淀粉样蛋白 PET**：直接显示脑内 Aβ 沉积（成本较高）

### 4. 实验室检查
- 血常规、生化、甲状腺功能、维生素 B12 等**排除可逆性病因**
- 脑脊液（CSF）检测 Aβ42、Tau、p-Tau 等生物标志物
- 基因检测（如 APOE ε4 等位基因）：辅助风险评估

### 5. AI 辅助分析
本系统采用 **TransMF 多模态融合模型**，结合 MRI 结构特征与 PET 代谢特征，输出 AD 风险评分，为医生提供客观参考。

> 最终诊断须由有经验的神经内科医生综合判断，AI 结果仅为辅助。""",
    },
    # ---------- 4. 治疗方案 ----------
    {
        "category": "treatment",
        "title": "药物与非药物治疗",
        "summary": "AD 目前无法根治，但规范的药物治疗与非药物干预可延缓认知衰退、改善生活质量。本文介绍主流治疗策略。",
        "tags": "治疗,药物,胆碱酯酶抑制剂,认知训练,康复",
        "icon": "FirstAidKit",
        "sort_order": 4,
        "content": """# 阿尔茨海默病的治疗方案

目前 AD **尚无法根治**，治疗目标是：**延缓认知衰退、维持生活质量、减轻照护负担**。治疗分为药物与非药物两大类，建议综合使用。

## 一、药物治疗

### 1. 改善认知的药物
- **胆碱酯酶抑制剂**（多奈哌齐、卡巴拉汀、加兰他敏）：适用于轻中度 AD，通过增加脑内乙酰胆碱改善认知
- **NMDA 受体拮抗剂**（美金刚）：适用于中重度 AD，调节谷氨酸神经传递
- **Aβ 抗体药物**（如仑卡奈单抗）：新型疾病修饰治疗，需早期使用且需评估副作用风险

### 2. 控制精神行为症状的药物
- **抗抑郁药**（SSRI 类）：改善抑郁、焦虑
- **非典型抗精神病药**：仅在严重幻觉、激越时短期使用，需警惕心血管副作用
- 避免使用苯二氮䓬类药物（可能加重认知障碍）

## 二、非药物干预

- **认知训练**：记忆训练、思维游戏、怀旧疗法
- **运动疗法**：规律有氧运动（如快走、太极），每周 150 分钟以上
- **社交活动**：参与集体活动，保持社交连接
- **音乐疗法**：舒缓情绪、改善睡眠
- **环境调整**：简化居家环境，减少跌倒风险

## 三、照护与支持

- 定期随访，动态调整治疗方案
- 照护者心理支持与喘息服务
- 提前规划法律与财务事宜

> 用药须在医生指导下进行，切勿自行停药或换药。""",
    },
    # ---------- 5. 护理建议 ----------
    {
        "category": "care",
        "title": "AD 照护者实用指南",
        "summary": "照护 AD 患者是一项长期挑战。本文从安全防护、沟通技巧、日常照护和照护者自身关怀四个方面提供实用建议。",
        "tags": "护理,照护者,居家安全,沟通技巧",
        "icon": "Avatar",
        "sort_order": 5,
        "content": """# AD 照护者实用指南

照护阿尔茨海默病患者是一项长期而艰巨的工作。以下建议帮助照护者更好地应对日常挑战。

## 一、居家安全防护

- **防走失**：安装门磁报警，给患者佩戴身份手环或定位设备
- **防跌倒**：移除地毯，安装扶手，保持地面干燥
- **防误食**：药品、清洁剂、尖锐物品上锁
- **防烫伤**：热水器调至 49℃ 以下，厨房使用后断气断电

## 二、沟通技巧

- **用简单句**：一次只说一件事，语速放慢
- **多用肯定语**：避免争论和纠正，患者记不起时不要反复追问
- **运用非语言沟通**：微笑、眼神接触、轻柔的肢体接触
- **识别情绪信号**：患者的激越常源于不适、恐惧或环境刺激，先排查原因

## 三、日常照护

- **建立规律作息**：固定的起床、用餐、活动、就寝时间
- **饮食均衡**：保证蛋白与水分摄入，晚期注意吞咽安全
- **协助穿衣**：选择易穿脱的衣物，按顺序摆放
- **如厕提醒**：定时引导如厕，减少失禁

## 四、照护者自我关怀

- **接受现实**：AD 不可逆，照护目标是维持生活质量而非"治好"
- **寻求支持**：加入家属互助团体，学会寻求喘息服务
- **关注自身健康**：定期体检，保持运动和社交
- **合理分工**：与家人分担照护任务，避免独力承担

> 照护者的健康同样重要。照顾好自己，才能更好地照顾患者。""",
    },
    # ---------- 6. 预防措施 ----------
    {
        "category": "prevention",
        "title": "降低 AD 风险的生活方式",
        "summary": "虽然 AD 无法完全预防，但研究表明健康的生活方式可显著降低发病风险。本文介绍 6 大可干预因素。",
        "tags": "预防,生活方式,运动,饮食,认知储备",
        "icon": "CircleCheck",
        "sort_order": 6,
        "content": """# 降低 AD 风险的生活方式

目前约 **三分之一** 的 AD 病例与可干预的生活方式因素相关。以下措施有助于降低发病风险、延缓认知衰老。

## 1. 坚持运动
- 每周 **150 分钟** 中等强度有氧运动（快走、游泳、骑行）
- 结合力量训练与平衡训练，每周 2-3 次
- 运动可改善脑血流、促进神经营养因子分泌

## 2. 健康饮食
- 推荐 **地中海饮食 / MIND 饮食**：多蔬果、全谷物、鱼类、坚果、橄榄油
- 减少红肉、加工食品、高糖高盐摄入
- 适量饮用绿茶、咖啡（研究显示可能有保护作用）

## 3. 保持社交与认知活跃
- 积极参与社交活动，维持人际连接
- 阅读、学习新技能、下棋、乐器演奏等增加"认知储备"
- 持续学习新事物可促进神经可塑性

## 4. 管理心血管危险因素
- 控制**血压、血糖、血脂**在正常范围
- 戒烟限酒
- 中年期高血压、糖尿病、肥胖显著增加晚年 AD 风险

## 5. 保证睡眠质量
- 每晚 7-8 小时高质量睡眠
- 睡眠中大脑可清除 Aβ 等代谢废物
- 睡眠呼吸暂停需积极治疗

## 6. 心理健康
- 管理压力，避免长期焦虑抑郁
- 培养兴趣爱好，保持积极心态
- 有抑郁症状及时就医

> 风险降低不等于完全免疫。对于有家族史或高风险人群，建议定期进行认知与影像学筛查。""",
    },
]


# ==================== 工具函数 ====================

def _ensure_seed(db: Session) -> None:
    """
    首次访问时若 knowledge_articles 表为空，初始化 6 篇预置科普文章。
    为保证本模块自包含（不依赖外部 init_db 注册模型），此处同时确保表已创建。
    """
    # 确保表存在（幂等：checkfirst=True）
    Base.metadata.create_all(bind=engine, tables=[KnowledgeArticle.__table__], checkfirst=True)

    # 表为空则写入种子数据
    count = db.query(KnowledgeArticle).count()
    if count > 0:
        return

    now = format_date_time()
    db.add_all([
        KnowledgeArticle(
            category=a["category"],
            title=a["title"],
            summary=a["summary"],
            content=a["content"],
            tags=a["tags"],
            icon=a["icon"],
            sort_order=a["sort_order"],
            is_published=True,
            created_at=now,
        )
        for a in SEED_ARTICLES
    ])
    db.commit()


def _article_to_dict(a: KnowledgeArticle) -> dict:
    """将 ORM 文章对象转为前端需要的 camelCase 字典"""
    return {
        "id": a.id,
        "category": a.category,
        "title": a.title,
        "summary": a.summary,
        "content": a.content,
        "tags": a.tags,
        "icon": a.icon,
        "createdAt": a.created_at,
    }


def _article_list_to_dict(a: KnowledgeArticle) -> dict:
    """列表项不返回 content（减少传输）"""
    return {
        "id": a.id,
        "category": a.category,
        "title": a.title,
        "summary": a.summary,
        "tags": a.tags,
        "icon": a.icon,
        "createdAt": a.created_at,
    }


# ==================== 端点 ====================

@router.get("/categories")
def get_categories(db: Session = Depends(get_db)):
    """分类列表（含各分类已发布文章数）"""
    _ensure_seed(db)
    # 按分类统计已发布文章数
    rows = (
        db.query(KnowledgeArticle.category)
        .filter(KnowledgeArticle.is_published == True)  # noqa: E712
        .all()
    )
    count_map: dict[str, int] = {}
    for (cat,) in rows:
        count_map[cat] = count_map.get(cat, 0) + 1
    data = [
        {
            "key": c["key"],
            "name": c["name"],
            "icon": c["icon"],
            "count": count_map.get(c["key"], 0),
        }
        for c in CATEGORIES
    ]
    return ok(data)


@router.get("/articles")
def list_articles(
    category: str = Query(default=""),
    keyword: str = Query(default=""),
    db: Session = Depends(get_db),
):
    """文章列表（支持按分类筛选 + 按标题/标签关键词搜索）"""
    _ensure_seed(db)
    q = db.query(KnowledgeArticle).filter(KnowledgeArticle.is_published == True)  # noqa: E712
    if category:
        q = q.filter(KnowledgeArticle.category == category)
    kw = keyword.strip()
    if kw:
        like = f"%{kw}%"
        q = q.filter(
            (KnowledgeArticle.title.like(like))
            | (KnowledgeArticle.tags.like(like))
        )
    rows = q.order_by(KnowledgeArticle.sort_order.asc(), KnowledgeArticle.id.asc()).all()
    return ok([_article_list_to_dict(a) for a in rows])


@router.get("/{article_id}")
def get_article(
    article_id: int,
    db: Session = Depends(get_db),
):
    """文章详情（含 Markdown 正文）"""
    _ensure_seed(db)
    a = (
        db.query(KnowledgeArticle)
        .filter(KnowledgeArticle.id == article_id, KnowledgeArticle.is_published == True)  # noqa: E712
        .first()
    )
    if not a:
        return fail("文章不存在", 404)
    return ok(_article_to_dict(a))
