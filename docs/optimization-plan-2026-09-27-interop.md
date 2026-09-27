# ADScreen 联网调研 · 互操作与架构对齐优化方案（2026-09-27 第三轮）

> 与前两轮的区别：
> - 第一轮/第二轮聚焦**工程可靠性**（依赖、并发、CSP、限流、CI、测试）——已落地，见 `optimization-plan-2026-09-27.md`；
> - 本轮聚焦**架构与互操作**：联网调研工业级医疗影像 AI 平台的参考架构，补齐 ADScreen 缺失的标准化对外能力。

---

## 一、联网调研：工业级平台的参考架构

### 1.1 医疗影像 AI 平台的标准分层（D/Vision Lab《Beyond the Algorithm》）

```
① Modalities / PACS / VNA / RIS / EHR
        ↓
② Integration & DICOM Gateway      ← DICOM / DICOMweb / HL7 / FHIR
        ↓
③ Workflow Orchestrator            ← 任务编排、优先级、模型选择
        ↓
④ AI Execution Services            ← 隔离的（常容器化）推理服务，CPU/GPU
        ↓
⑤ Results Management               ← 输出转 DICOM SEG / DICOM SR / FHIR
        ↓
⑥ Viewer & Clinical Validation     ← 阅片与人工确认
        ↓
⑦ Monitoring / Audit / Governance  ← 指标、日志、版本、审计
```

原文最关键的一句：

> **"从原型到产品的真正转变，发生在算法不再是一条孤立流水线、而成为更广泛工作流一部分的时候。"**

分层带来的收益：新增模型不必改 DICOM 网关或阅片器；接入新 PACS 不必动算法。

### 1.2 互操作标准（2026 年事实标准）

| 标准 | 作用 | 关键路由/资源 |
| --- | --- | --- |
| **DICOMweb** | 影像访问 | `QIDO-RS` 查询 / `WADO-RS` 取像素与元数据 / `STOW-RS` 存回 |
| **FHIR R4** | 临床上下文 | `Patient` / `ImagingStudy` / `DiagnosticReport` / `Observation` / `ServiceRequest` |
| **SMART on FHIR** | EHR 内 OAuth 启动 | 让 AI 应用在临床医生的 EHR 上下文中打开 |
| **CDS Hooks** | 工作流时刻触发决策支持 | 在开单/阅片节点推送建议 |

行业共识（Fora Soft 2026 Playbook / Nirmitee 架构指南）：

> "DICOMweb 负责搬影像，FHIR R4 负责搬医嘱、报告与临床上下文——两者合起来才是 2026 年真正的 EHR 集成。
> 跳过它们，等于用私有适配器把影像硬绑到医疗栈上，**每接一家医院就要重做一遍集成**。"

### 1.3 同类项目在架构维度上的位置

| 项目 | ②集成层 | ③编排 | ④推理隔离 | ⑤结果管理 | ⑥阅片 | 优势 | 不足 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| **OHIF** | DICOMweb + 可插拔 DataSource | — | — | 读 SR/SEG | ★★★★★ | extensions/modes/services 三层解耦；hanging protocol 引擎；Cornerstone3D GPU 渲染 | 无病例管理、无报告生成、无随访闭环 |
| **Orthanc** | DICOM + DICOMweb + 插件 | Lua 脚本 | — | — | 无前端 | 轻量、纯 REST、插件化、多存储后端 | 无 AI、无临床业务 |
| **MONAI Deploy** | DICOM 输入 | MAP 编排 | ★★★★★ 容器隔离 | MAP 输出 | — | 推理与主服务解耦，OOM 不拖垮主站 | 纯基础设施，无临床 UI |
| **MONAILabel** | DICOM | 主动学习编排 | 容器内 | DICOM SEG | 集成 OHIF/Slicer | AI 预标注→医师修正→增量重训闭环 | 标注工具定位，无诊断分期/报告 |
| **Kaapana** | DICOM | ★★★★★ Airflow | 容器 | — | 集成 OHIF | 工作流编排 + 多中心数据治理 | 依赖 K8s，运维门槛高 |
| **dcm4chee** | ★★★★★ 完整 DICOM 语义 | — | — | — | — | 归档语义最完备 | 重部署、无 AI |
| **ADScreen（当前）** | ❌ 无标准接口 | 内存队列 | ❌ 同进程 | CSV/JSON | 自建（MPR/3D/ROI） | 业务闭环最完整 | **对外互操作为零** |

---

## 二、本项目差距（对照参考架构实测）

| 层 | 现状实测 | 差距 |
| --- | --- | --- |
| ② 集成层 | `grep -rn "fhir\|DiagnosticReport\|ImagingStudy"` → 0 命中；`grep -rni "dicomweb\|qido\|wado\|stow"` → 0 命中 | **完全没有标准互操作接口**，对外只能通过 CSV/JSON 文件交换 |
| ③ 编排层 | `services/batch_task.py` 为 `dict` 内存单例 + `threading.Lock`，`main.py` 已自告警"多 worker 不共享" | 无法水平扩展；进程重启任务丢失 |
| ④ 推理隔离 | torch 推理与主服务同进程（`services/model_inference.py`） | OOM 会拖垮整个站点 |
| ⑤ 结果管理 | `routers/export.py` 仅 CSV（`/cases`、`/analysis`） | 无 DICOM SR / FHIR 结构化输出，结果无法回写 EHR |
| ⑦ 治理 | Docker 镜像以 **root** 运行（Dockerfile 无 `USER` 指令），同时跑 nginx + uvicorn | 容器逃逸即获宿主机 root，医疗合规红线 |

---

## 三、实施方案

### P0 · 互操作层补齐（本轮实施）

1. **FHIR R4 导出服务**（`services/fhir_service.py` + `routers/interop.py`）
   - `Patient`：由 `case_records.patient_json` 映射（patientNo 作 identifier、name、gender、age→birthDate 推算）
   - `ImagingStudy`：由 modality / exam_date / dicom_meta 映射，含 `procedureCode` 与 modality 的 coding
   - `DiagnosticReport`：NIA-AA 分期（`stage` / `stageCode` / `biologicalStage`）作 conclusion，附 `result` 引用
   - `Observation`：风险评分、海马体积 L/R、平均 SUV、皮层厚度、脑室体积、MTA 评分——量化指标以标准编码输出
   - `Bundle`：`searchset` 类型聚合上述资源，支持按 cohort / riskLevel 批量导出
   - 审计：复用 `_audit_export` 范式，每次 FHIR 导出落 SystemLog

2. **DICOMweb 只读接口**（`services/dicomweb_service.py` + `routers/dicomweb.py`）
   - `GET /api/dicomweb/studies`（QIDO-RS）：从 `case_records.dicom_meta` 构造 DICOM JSON studies
   - `GET /api/dicomweb/studies/{study}/series`（QIDO-RS series）
   - `GET /api/dicomweb/studies/{study}/series/{series}/instances/{instance}/metadata`（WADO-RS metadata）
   - 支持 `Accept: application/dicom+json` 与标准 `includefield` / `limit` / `offset` 查询参数
   - **不做 STOW-RS**（写入需完整归档语义与幂等保证，列为 P2）

### P1 · 编排与部署加固（本轮实施）

3. **批量任务持久化**：新增 `batch_task_records` 表，把 `BatchTaskManager` 的内存 dict 换成 DB 存储，
   保留原有公开方法签名（`create_task` / `get_task` / `list_tasks` / `start_task` / `update_progress` / `cleanup_stale`），
   使多 worker 部署可用，并移除 `main.py` 中的多 worker 告警前提。

4. **容器非 root 运行**：Dockerfile 新增 `adscreen` 系统用户，nginx 与 uvicorn 均以非 root 运行；
   数据/日志卷属主同步调整。

### P2 · 后续建议（本轮不实施，改动面大）

- STOW-RS 写入 + 完整 DICOM 归档语义（对标 dcm4chee）
- DICOM SR / DICOM SEG 输出（对标 MONAI Deploy 的 Results Management）
- 推理拆独立容器（MONAI Deploy MAP 思路）
- SMART on FHIR + CDS Hooks 临床集成
- Alembic 迁移替代手写 `_migrate_columns`（现覆盖 2/16 表）

---

## 三·补、本轮落地结果（实测）

| # | 条目 | 状态 | 落地位置 |
| --- | --- | --- | --- |
| 1 | FHIR R4 导出 | ✅ | `services/fhir_service.py` + `routers/interop.py`；4 个端点（metadata / patient / diagnostic-report / bundle） |
| 2 | DICOMweb 只读 | ✅ | `services/dicomweb_service.py` + `routers/dicomweb.py`；QIDO-RS studies/series/instances + WADO-RS metadata，STOW-RS 显式 501 |
| 3 | 批量任务持久化 | ✅ | 新增 `models/batch_task.py`（`batch_task_records` 表）；`services/batch_task.py` 由内存 dict 改为 DB 存储，对外签名不变 |
| 4 | 容器非 root | ✅ | Dockerfile 新增 `adscreen` 用户（uid 10001），Nginx 改监听 8080（非 root 无法绑 80），compose 端口映射同步 |
| 5 | 回归测试 | ✅ | 新增 `tests/test_interop_round3.py`（30 项），全量 **133 passed** |

### 测试抓到并修复的真实缺陷

1. **DICOM UID 超长**：初版生成的 UID 为 67 字符，超过 DICOM 规定的 64 字符上限——
   `dcm4chee`/`Orthanc` 等归档系统会直接拒绝。改为逐段拼接 8 位十六进制（每段 ≤10 位十进制），
   拼不下即停，实测 ≤64。
2. **`list_active` 语义**：经复核确认「队列深度」应含 pending（等待中亦占队列），
   修正测试断言而非改实现，并在测试 docstring 中固化语义。

### 端到端实测记录

```
ruff check --select=E9,F63,F7,F82,F401,E711,E712 .  → All checks passed
pytest tests/ -q                                     → 133 passed

GET /api/interop/fhir/metadata → 200  fhirVersion=4.0.1
                                      resources=[Patient, ImagingStudy, Observation, DiagnosticReport]
GET /api/interop/fhir/bundle?limit=3  → 200  type=searchset, total=6
GET /api/dicomweb/studies?limit=2     → 200  ModalitiesInStudy=['MR','PT']
                                            StudyInstanceUID=1.2.826...2295950042.2838834532.3722092704
POST /api/dicomweb/studies            → 501  STOW-RS 未实现
无 token 访问                          → 401
```

> 说明：演示库 47 例病例中 `analysis_records` 为 0（未跑 AI 分析），
> 因此 Bundle 只含 Patient + ImagingStudy，不产出 DiagnosticReport——
> 这是**正确行为**（无分析不产出临床结论），已由
> `test_bundle_without_analysis_still_has_patient` 固化。

## 四、验证方式

```bash
cd ad-screen-backend
python -m pytest tests/ -q                       # 含本轮新增互操作测试
ruff check --select=E9,F63,F7,F82,F401,E711,E712 .

# FHIR 导出
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/api/interop/fhir/bundle?riskLevel=mci" | python -m json.tool

# DICOMweb QIDO-RS
curl -H "Accept: application/dicom+json" \
  -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/api/dicomweb/studies?limit=5"
```
