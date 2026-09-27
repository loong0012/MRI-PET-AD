# AD-Screen 诊断结果 7 大类优化方案

## Context（背景与目标）

用户基于近 3-5 年顶刊/arXiv 前沿 AI 辅助 AD 诊断文献，将诊断结果呈现归纳为 7 大类：
1. 分类标签+置信概率；2. 影像可解释可视化；3. 定量指标报表；4. 结构化临床文本报告；
5. 风险等级仪表盘；6. 案例检索式对比；7. 预后预测。

**项目现状评估**（已通过 Explore 子代理核查）：
- ✅ **已完整覆盖**：第 1 类（riskScore/riskLevel/ensembleProb/probMin/Max/Std）、第 2 类（Grad-CAM + 异常脑区热力图 + 3D 体绘制 + ROI 标注）、第 7 类（`PrognosisPanel.vue` + `routers/prognosis.py` 已完整实现 12/24/36 月 MMSE 预测 + 转_AD 时间窗 + 轨迹图，**勿动**）
- 🟡 **需补强**：第 3 类（无雷达图）、第 4 类（无 NIA-AA 对齐 / 矛盾证据 / 患者科普版）、第 5 类（无认知趋势 widget / 风险百分位 / 特征重要性）、第 6 类（无 top-k 自动检索，仅手动 2 例对比）

**用户确认范围**：4 项全做（第 3、4、5、6 类），第 4 类报告生成方式为**纯规则+模板驱动**（不引入 LLM 依赖）。

**预期产出**：
- 论文/毕设可引用的前沿功能演示点
- 不破坏既有功能与硬件约束（PC-only、软删除、统一响应、严格 TS）

---

## 复用清单（避免重复造轮子）

- `ok(data, message)` / `fail(message, code)`：[schemas/common.py](file:///d:/Desktop/TransMF_AD-master/ad-screen-backend/schemas/common.py#L19-L26) L19-26
- `case_record_to_dict(rec)`：[services/data_init.py](file:///d:/Desktop/TransMF_AD-master/ad-screen-backend/services/data_init.py#L145-L164) L145-164（含 `patient {patientNo, name, gender, age}` 规范字段）
- `parseRefRange(ref)`：[MetricsFullscreenViewer.vue](file:///d:/Desktop/TransMF_AD-master/ad-screen-frontend/src/components/MetricsFullscreenViewer.vue#L89-L99) L89-99（解析 `"5.5-8.5 cm³"` → `{low, high, mode}`）
- `KEY_CARD_MAP`：同文件 L57-64（已映射 6 个核心指标维度）
- `_case_filter(db, modality)` + `_validate_modality(modality)`：[analytics.py](file:///d:/Desktop/TransMF_AD-master/ad-screen-backend/routers/analytics.py#L39-L49) L39-49
- 月份分桶 + 最近随访模式：`get_monthly_trend` L222-248、`latest_visits` L296-300
- `ChartBase.vue`：ECharts 封装，`@/components/ChartBase.vue`（DashboardView 已用）
- Dashboard `ALL_WIDGETS` + `SEEN_KEY` 自动迁移：[DashboardView.vue](file:///d:/Desktop/TransMF_AD-master/ad-screen-frontend/src/views/dashboard/DashboardView.vue#L31-L73) L31-73（**当前有 5 个 widget**：todo/stats/trend/dist/tasks，新 widget 自动 prepended 给既有用户）
- AnalysisResult 全字段：[types/analysis.d.ts](file:///d:/Desktop/TransMF_AD-master/ad-screen-frontend/src/types/analysis.d.ts#L23-L57) L23-57（含 hippocampusVolumeL/R、meanSUV、corticalThickness、ventricleVolume、mtaScore、abnormalRegions[]、metrics[]、riskScore、riskLevel、stageCode）

---

## 改造 A：报告系统升级（第 4 类）

### 后端

**A1. 扩展 ReportTemplate 字段**（[models/report_template.py](file:///d:/Desktop/TransMF_AD-master/ad-screen-backend/models/report_template.py)）
- 默认 `content_structure` JSON 字段从 10 个 → 13 个，新增：
  - `niaaAlignment`（NIA-AA 框架对齐段，bool 默认 False）
  - `conflictEvidence`（影像-认知矛盾证据段，bool 默认 False）
  - `patientFriendly`（患者通俗科普版正文，bool 默认 False）

**A2. 更新种子模板**（[routers/report_template.py](file:///d:/Desktop/TransMF_AD-master/ad-screen-backend/routers/report_template.py) L88-137 `_build_default_templates`）
- `standard`（标准）：`niaaAlignment=True, conflictEvidence=True`
- `brief`（简明）：保持不变
- `research`（科研）：3 个新字段全部 True
- **新增第 4 套 `patient` 模板**：仅启用 `patientFriendly`，字段集最小（结论 + 通俗解释 + 随访建议）

**A3. 报告聚合时计算新字段**（[routers/report.py](file:///d:/Desktop/TransMF_AD-master/ad-screen-backend/routers/report.py) L38-91 `get_report_data`）
- 新增 `FollowUpVisit` 到 import（已有 `FollowUpRecord`）
- 查询该病例最近一次 FollowUpVisit（按 `visit_date.desc()` 取首条），用于认知-影像矛盾判定
- 在 `return ok(...)` 前计算 3 个字段，加入返回 dict：
  - `niaaAlignment: {A, T, N, reasoning}`
    - **A**（amyloid）：无 CSF Aβ → 用 PET SUV 代理；`meanSUV < 0.85` → `"A+"`，否则 `"A-"`；缺数据 → `"A_unknown"`
    - **T**（tau）：无 CSF p-tau → 用海马萎缩代理；遍历 `abnormalRegions`，匹配 `region.contains("海马")` 且 `zScore < -2` → `"T+"`，否则 `"T-"`
    - **N**（neurodegeneration）：`hippocampusVolumeL + hippocampusVolumeR < 6.0`（正常下限）→ `"N+"`，否则 `"N-"`
    - `reasoning`：拼接简短文本说明 A/T/N 标签依据
  - `conflictEvidence: {hasConflict, items[]}`
    - 项 1（影像-认知）：`riskLevel in ("ad-early","ad-late")` 且最新随访 `mmse > 24` → 矛盾
    - 项 2（模态内部）：海马重度萎缩 + `meanSUV > 0.9`（PET 代谢正常）→ 矛盾
    - `items: [{type, description}]`，`hasConflict = len(items) > 0`
  - `patientFriendly: {summary, lifestyle[], followUp}`
    - 基于 `stageCode` 映射表（CN/MCI/AD-E/AD-L → 通俗描述 + 生活方式建议清单 + 随访提示）
- 数据缺失时全部返回 None，前端 v-if 容错

### 前端

**A4. 扩展类型**（[types/report.d.ts](file:///d:/Desktop/TransMF_AD-master/ad-screen-frontend/src/types/report.d.ts)）
- `ReportData` 增 3 个可选字段：`niaaAlignment?: {A,T,N,reasoning} | null`、`conflictEvidence?: {hasConflict,items[]} | null`、`patientFriendly?: {summary,lifestyle[],followUp} | null`
- `ReportTemplateType` 联合类型加 `'patient'`

**A5. ReportTemplateDialog 增字段勾选**（[components/ReportTemplateDialog.vue](file:///d:/Desktop/TransMF_AD-master/ad-screen-frontend/src/components/ReportTemplateDialog.vue)）
- `FIELD_OPTIONS` 加 3 项（label/desc/key）
- `TYPE_OPTIONS` 加 `patient`
- `DEFAULT_FIELDS` 同步后端

**A6. ReportView 新增 3 个 section + 患者版切换**（[views/report/ReportView.vue](file:///d:/Desktop/TransMF_AD-master/ad-screen-frontend/src/views/report/ReportView.vue)）
- 顶部工具栏（L322 附近）加 `el-switch`：`patientMode` (true=患者版, false=医生版)
- 现有所有医生版 section 加 `v-if="!patientMode"`
- 新增 3 个 `v-if` section（仅医生版显示）：
  - **NIA-AA 框架对齐**：3 个彩色 chip（A 绿/灰、T 黄/灰、N 红/灰）+ reasoning 文本
  - **矛盾证据**：`el-alert type="warning"` 列出 `items[].description`，无矛盾时显示绿色"未发现矛盾证据"提示
  - **患者通俗版预览**：单独 section，含 summary + lifestyle 列表 + followUp 提示
- 新增患者版专属 section `v-if="patientMode"`：渲染 `patientFriendly` 三个字段，使用通俗文案样式
- PDF/打印兼容：`printSection()` 目标 `.print-area`（L340），切换 patientMode 时仅当前模式 section 在 DOM 中，导出无冲突

---

## 改造 B：多维度雷达图（第 3 类）

**仅改 1 个文件**：[components/MetricsFullscreenViewer.vue](file:///d:/Desktop/TransMF_AD-master/ad-screen-frontend/src/components/MetricsFullscreenViewer.vue)

- L13 后新增 `import ChartBase from '@/components/ChartBase.vue'`
- 新增 `radarOption` computed：
  - 6 维 indicators：`[{name: '左海马体积', max}, {name: '右海马体积', max}, {name: 'PET SUV', max}, {name: '皮层厚度', max}, {name: 'MTA 评分', max}, {name: '脑室体积', max}]`
  - 复用 `KEY_CARD_MAP`（L57-64）映射 AnalysisResult 字段 → 维度
  - 复用 `parseRefRange(metric.refRange)` 提取每维 low/high，归一化到 0-max（max=high*1.2 留余量）
  - 2 个 series：
    - 患者实测值（蓝色填充半透明）
    - 参考范围均值 (low+high)/2（灰色虚线）
  - 处理 `parseRefRange` 返回的 `mode`：仅 `range` 模式给干净 low/high；其他模式 fallback `low=0, high=value*2`
- 模板：在 L392（异常脑区分布）之后、L394（全数量化指标）之前插入 `<ChartBase :option="radarOption" :height="320" />`
- 配色与现有图表一致（医学蓝灰主题）

---

## 改造 C：相似病例 top-k 推荐（第 6 类）

### 后端

**C1. 新增相似病例端点**（[routers/case.py](file:///d:/Desktop/TransMF_AD-master/ad-screen-backend/routers/case.py)）
- **路由位置**：插入 L504 `/{case_id}` GET 之前（项目惯例：具体路径在通配前，见 L540 `/batch` 与 L583 `/{case_id}` DELETE 的顺序）
- `GET /case/{case_id}/similar?limit=3`
- 算法：
  - 拉取目标病例的 AnalysisRecord（`analysis_records.case_id == case_id`，按 id desc 取首条），解析 `result_json` 得 `riskLevel`/`stageCode`/`abnormalRegions`/`patient age+gender`
  - 候选集：所有 `CaseModel.is_deleted == False` 且 `AnalysisRecord.case_id == CaseModel.id`（即有分析记录）且 `case_id != 目标`
  - 每个候选计算相似度分（满分量级 ~10）：
    - `riskLevel` 精确匹配：+3；相邻（CN↔MCI、MCI↔AD-E、AD-E↔AD-L）：+1
    - 异常脑区集合 Jaccard × 3（修正：原方案 ×5 过权重）
    - 年龄差 ≤ 5：+1
    - 性别匹配：+1
    - 检查日期间隔 ≤ 6 个月：+0.5
  - 边界：双方 `abnormalRegions` 均空 → Jaccard 返回 0（避免 NaN）；候选缺 `riskLevel` → 跳过
- 返回 top `limit` 个：`[{caseId, patientName, age, gender, riskScore, riskLevel, stageCode, abnormalRegionCount, examDate}]`，统一 `ok(...)` 包装

### 前端

**C2. API 与类型**
- [api/case.ts](file:///d:/Desktop/TransMF_AD-master/ad-screen-frontend/src/api/case.ts)：`apiGetSimilarCases(caseId: string, limit = 3): Promise<SimilarCase[]>` 走 `httpGet`
- [types/case.d.ts](file:///d:/Desktop/TransMF_AD-master/ad-screen-frontend/src/types/case.d.ts) 新增：
  ```ts
  interface SimilarCase {
    caseId: string; patientName: string; age: number; gender: 'M' | 'F'
    riskScore: number; riskLevel: RiskLevel; stageCode: 'CN'|'MCI'|'AD-E'|'AD-L'
    abnormalRegionCount: number; examDate: string
  }
  ```

**C3. 诊断页新增"相似病例参考"卡片**（[views/analysis/AnalysisDetailView.vue](file:///d:/Desktop/TransMF_AD-master/ad-screen-frontend/src/views/analysis/AnalysisDetailView.vue)）
- 在 AI 综合诊断摘要卡片下方（行 2 与行 3 之间）插入新 card-ad
- `onMounted` 内并行调 `apiGetSimilarCases(caseId.value, 3)`，loading 时显示 `el-skeleton` 3 列
- 3 个 mini `el-card` 横排：caseId + 患者名 + RiskLevelTag + 异常脑区数 + 检查日期
- 点击整卡 → `router.push('/analysis/' + caseId)`

---

## 改造 D：Dashboard 认知趋势 widget（第 5 类）

### 后端

**D1. 新增认知趋势端点**（[routers/analytics.py](file:///d:/Desktop/TransMF_AD-master/ad-screen-backend/routers/analytics.py) L248 后插入）
- `GET /analytics/cognitive-trend?months=12`
- 复用 `_case_filter(db, None)` 取非软删病例 id 集
- JOIN `FollowUpVisit.case_id in (ids)`，按 `visit_date[:7]` 月份分桶
- 每月聚合：`avgMMSE`、`avgMoCA`、`patientCount`、`adConversionCount`（关联病例 `risk_level in ('ad-early','ad-late')` 计数）
- 返回 `[{month, avgMMSE, avgMoCA, patientCount, adConversionCount}]`，按月份升序

### 前端

**D2. API 与类型**
- [api/analytics.ts](file:///d:/Desktop/TransMF_AD-master/ad-screen-frontend/src/api/analytics.ts)：`apiGetCognitiveTrend(months = 12)`
- 新增 `CognitiveTrendPoint` 类型：`{month: string; avgMMSE: number|null; avgMoCA: number|null; patientCount: number; adConversionCount: number}`

**D3. DashboardView 新增 widget**（[views/dashboard/DashboardView.vue](file:///d:/Desktop/TransMF_AD-master/ad-screen-frontend/src/views/dashboard/DashboardView.vue)）
- L31-37 `ALL_WIDGETS` 增第 6 项 `{key: 'cognition', label: '认知趋势', defaultVisible: true}`
- `SEEN_KEY` 自动迁移（L64-73）会为新 widget 在既有用户配置中 prepend，无需手动改 localStorage
- 模板：`v-for="wkey in visibleWidgetKeys"` 循环中（L399 附近），在 `trend` 分支后加 `v-else-if="wkey === 'cognition'"` 分支
- 渲染 `ChartBase` 双折线：MMSE 实线（蓝）+ MoCA 虚线（橙），X 轴月份、Y 轴分数 0-30，加 adConversionCount 阴影区背景
- onMounted 拉取数据，`loading` 时显示 el-empty 占位
- "Restore Default" 自动包含 cognition（既有逻辑无需改）

---

## 约束与边界

- **严格 TS**：所有新字段用 `field?: Type | null`，禁止 `any`；`parseRefRange` 返回 `{low, high, mode}` 均为 number，安全
- **PC-only**：所有 UI 按 1280+ 桌面布局设计，无响应式断点
- **软删除**：相似病例 / 认知趋势 均过滤 `is_deleted == False`
- **统一响应**：后端所有新端点返回 `ok(data)` / `fail(msg)`
- **FastAPI 路由顺序**：`/case/{case_id}/similar` 必须在 `/case/{case_id}` 之前
- **HMR 兼容**：Dashboard widget 走 v-for + v-else-if 模式，新增分支安全；ReportView 加 section 不破坏既有打印
- **不改动既有功能**：PrognosisPanel、CaseListView 2 例对比、MedicalViewport 热力图均不触碰

---

## 验证方案

### 类型检查
```powershell
cd d:\Desktop\TransMF_AD-master\ad-screen-frontend
npx vue-tsc --noEmit
# 期望 exit code 0
```

### 后端冒烟
```powershell
# 确认 8000 端口在跑
$env:PYTHONIOENCODING="utf-8"
python -c "from main import app; print('routes:', len(app.routes))"
# 期望打印路由数（应为原数 + 2：similar + cognitive-trend）
```

### 浏览器端到端
1. 登录 admin/admin123 → 访问 `/analysis/AD260001`
2. **雷达图**：点开"全屏量化分析"，最下方应出现 6 维 radar，患者蓝填充 + 参考灰虚线
3. **相似病例**：AI 摘要下方应有"相似病例参考"卡片，3 个 mini case 横排，点击跳转
4. **报告升级**：访问 `/report/AD260001`，顶部应有"医生版/患者版"switch；医生版下显示 NIA-AA chips + 矛盾证据 alert + 患者通俗版预览 section；切到患者版只显示通俗文案
5. **Dashboard 认知趋势**：访问 `/dashboard`，新 widget "认知趋势" 应自动出现（既有用户 SEEN_KEY 自动 prepend），双折线 MMSE/MoCA
6. 控制台无新增错误（既有 `/api/notification/unread-count` 噪音可忽略）
