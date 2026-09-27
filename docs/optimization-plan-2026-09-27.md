# ADScreen 对标开源同类项目 · 优化完善方案（2026-09-27 轮次）

> **本文件取代 `docs/optimization-plan-2026-09.md`**。
> 原因：上一轮方案在文档末尾声明「已实施 P0/P1 全部条目」，但本次对代码库做逐条复核后确认——
> **该轮仅产出文档，代码零落地**（详见 §4 实测证据）。本轮方案在保留其调研结论的基础上，
> 重新做了一次 GitHub 实证调研，并给出**可逐条验证**的实施清单与本轮真实落地结果。

---

## 一、调研方法与样本

| 维度 | 做法 |
| --- | --- |
| 数据源 | GitHub REST API v3（`search/repositories` + `repos/{owner}/{repo}`），检索日期 2026-09-27 |
| 检索式 | `alzheimer MRI PET deep learning`、`topic:alzheimer-detection`、`topic:alzheimer`、`medical imaging platform dicom web viewer`、`topic:medical-imaging AND topic:deep-learning`、`dicomweb pacs orthanc` |
| 定向核验 | OHIF / MONAI / MONAILabel / monai-deploy / nnUNet / MedSAM / Slicer / Kaapana / dcm4chee 的 star、语言、最近推送时间 |
| 本地审计 | 后端 16.6k 行 / 31 路由 / 15 服务 / 16 ORM 模型，AST 全量扫描第三方依赖，前端 120 个 .vue/.ts |

---

## 二、同类项目画像（实测数据）

### 2.1 通用医学影像平台（工业级，可作为工程范式来源）

| 项目 | Star | 语言 | 最近更新 | 定位 | 值得借鉴 | 不足 |
| --- | --- | --- | --- | --- | --- | --- |
| **MIC-DKFZ/nnUNet** | 8911 | Python | 2026-09-25 | 自配置医学分割 | **dataset fingerprint → 自动推导 pipeline**；无需手工调参；标准化评估与 ensembling | 纯分割算法，无临床业务 |
| **Project-MONAI/MONAI** | 8711 | Python | 2026-09-26 | 医疗影像 AI 工具包 | transforms/数据管线标准化、模型 zoo、与 PyTorch 生态无缝 | 算法库，无产品外壳 |
| **bowang-lab/MedSAM** | 4405 | Notebook | 2025-05-07 | 医学通用分割基座 | 基座模型 + 提示式交互 | 无临床闭环，已停更 |
| **OHIF/Viewers** | 4349 | TypeScript | 2026-09-25 | 零足迹 Web DICOM 阅片器 | **extensions / modes / services 三层解耦**；datasource 抽象（DICOMweb / 本地 / 静态）；hanging protocols 布局协议 | 纯阅片器：无病例管理、无报告、无随访、无随访干预闭环 |
| **Slicer/Slicer** | 2636 | C++ | 2026-09-26 | 桌面影像计算平台 | 插件化模块体系、Python 脚本化扩展 | 桌面应用，非 Web，无法多中心协同 |
| **Project-MONAI/MONAILabel** | 892 | Python | 2026-09-22 | 智能标注 + 主动学习 | **主动学习闭环**：AI 预标注 → 医师修正 → 增量重训；与 Slicer/OHIF 集成 | 标注工具定位，无诊断分期/报告/随访 |
| **dcm4chee-arc-light** | 502 | Java | 2026-09-25 | DICOM 归档（PACS 核心） | DICOM 标准语义完备、存储分层 | 重部署、无 AI、无前端 |
| **kaapana/kaapana** | 276 | Python | 2026-09-25 | K8s 影像分析平台 | **工作流编排（Airflow）+ 容器化算法挂载**；多中心数据治理 | 依赖 K8s，运维门槛高；无 AD 业务语义 |
| **monai-deploy** | 119 | Shell | 2025-03-26 | AI 应用打包标准 | **MAP（MONAI Application Package）**：推理容器与主服务隔离，OOM 不拖垮主站 | 基础设施向，无临床 UI |

### 2.2 AD 垂类（与本项目最直接可比）

| 检索式 | 结果 |
| --- | --- |
| `topic:alzheimer-detection` | **共 14 个仓库，最高 1★** |
| `alzheimer MRI PET deep learning`（按 star 排序前 15） | **全部 0★** |
| `topic:alzheimer stars:>50` | 仅 4 个，且均非 AD 平台（TPOT 等误命中） |

抽样画像：

| 项目 | 形态 | 评价 |
| --- | --- | --- |
| `JNT-h04/xai-alzheimer-early-detection` | ResNet50 四分类 + Grad-CAM/SHAP | 有可解释性，但止于 notebook |
| `VithyabavanS/alz-insightnet` | JS 前端 + 可解释 AI | 最接近平台化，仍是原型 |
| `MeetInCode/AI-4-Alzheimer-s` | FastAPI + Next.js + nnU-Net + MedGemma + RAG，**0★** | 架构思路新（LLM+RAG 生成报告），值得关注其"报告自动生成"路径 |
| `DA-workshop-101/Alzheimer-Stages-Classification` | VGG19 + FastAPI + Grad-CAM + MLflow + GitHub Actions CI/CD | **工程实践最规范**：双 requirements（dev/prod）、CI/CD、容器化——正是本项目缺口 |

### 2.3 结论

1. **AD 垂类开源生态几乎空白**：没有任何一个仓库同时具备"多模态融合推理 + 病例管理 + 报告 + 随访 + 多中心分析"。本项目在**功能完整度上是该细分方向的天花板**。
2. **差距不在功能，在工程可靠性**。`DA-workshop-101` 那个 0★ 项目反而在 CI/CD、依赖分层、容器化上做得比本项目规范——这是最刺眼的对比。
3. 可迁移的 5 条工程范式：
   - **OHIF** → 分层解耦（本项目已有 routers/services/models 分层，达标）
   - **MONAI Deploy MAP** → 推理进程隔离（本项目 P2）
   - **MONAILabel** → 主动学习闭环（本项目已有 `active_learning.py` 路由，达标）
   - **nnUNet** → 自配置与标准化评估（本项目已有集成推理与温度校准，达标）
   - **DA-workshop-101** → **双 requirements + CI/CD（本项目完全缺失，P0/P1 重点）**

---

## 三、本项目实测差距清单

> 以下每条均给出**代码级证据**，可逐条复现验证。

### P0 · 部署阻断级

| # | 问题 | 证据 | 危害 |
| --- | --- | --- | --- |
| 1 | `requirements.txt` 仅 7 包，实际依赖 9 个第三方库 | AST 扫描后端全部 `.py` 得到第三方顶层包：`PIL, bcrypt, dateutil, monai, nibabel, numpy, pydicom, scipy, torch`；而 `ad-screen-backend/requirements.txt` 只有 fastapi/uvicorn/sqlalchemy/pydantic/python-jose/passlib/python-multipart | Docker 镜像按此构建 → 启动即 `ModuleNotFoundError`（连 `services/data_init.py` 的 `dateutil` 都缺） |
| 2 | `passlib[bcrypt]==1.7.4` 已弃用且未被使用 | `services/auth.py:16` 直接 `import bcrypt`，无 passlib 引用 | 装了一个死依赖，且 passlib 1.7.4 与 bcrypt ≥4.1 有已知兼容告警 |
| 3 | SQLite 未开 WAL / busy_timeout / 外键 | `database.py:12-16` 仅 `check_same_thread=False` | 批量推理写库与 Web 读库互斥 → `database is locked` |
| 4 | compose 卷与 DB 路径不匹配 | `docker-compose.yml` 挂 `adscreen-db:/app/backend/data`，但 `config.py:22` 默认 `ad-screen-backend/ad_screen.db` | 容器重建即丢库 |
| 5 | compose 占位密钥可绕过安全校验 | `docker-compose.yml` 默认 `please-change-this-secret-key-in-production-env`（48 字符 ≥32）→ `config.py:67` 长度检查放行 | 生产环境用公开字符串签发 JWT |
| 6 | 演示弱口令生产也写入 | `data_init.py:24-28`：`admin/admin123`、`rad01/123456` 无条件写入 | 医疗系统默认弱口令，合规红线 |

### P1 · 安全与工程化

| # | 问题 | 证据 |
| --- | --- | --- |
| 7 | CSP 完全缺失 | `main.py` 安全头中间件注释自认「CSP 暂用宽松策略」，实际未下发任何 CSP |
| 8 | 全局兜底限流缺失 | `rate_limit(` 仅出现在 `auth.py`(3) / `export.py`(2) / `analysis.py`(1) 共 6 个端点，其余 31 路由无限流 |
| 9 | 无 CI，且非 git 仓库 | 无 `.github/`；`git rev-parse` 报 not a git repository |
| 10 | 前端无组件级错误边界 | `main.ts:31-38` 仅有全局 `errorHandler` 打印日志，子树渲染异常 → 白屏 |
| 11 | `.gitignore` 覆盖不全 | 缺 `ad-screen-backend/.env`、`*.db-wal`、`*.db-shm`、`dist/` |
| 12 | 根目录垃圾文件 | 存在 0 字节文件 `=`（误操作产物） |

### P2 · 建议不本轮实施（改动面大）

- `_migrate_columns` 仅覆盖 `users`/`case_records` 2/16 表 → 引入 Alembic
- 批量任务为内存单例（`batch_task.py`），多 worker 不共享 → Redis/Celery（`main.py` 已有告警）
- 推理拆独立进程（MONAI Deploy MAP 思路），隔离 torch OOM
- 前端 vitest + Playwright E2E、vue-i18n

---

## 四、上一轮方案的落地复核（重要）

| 上轮声明已实施 | 实际状态 |
| --- | --- |
| 补全 requirements.txt | ❌ 仍为原 7 包 |
| SQLite WAL + busy_timeout | ❌ `database.py` 无 PRAGMA |
| compose 卷对齐 | ❌ 未注入 `ADSCREEN_DB_PATH` |
| 生产演示数据开关 | ❌ 无 `ADSCREEN_SEED_DEMO_DATA` |
| 封堵占位密钥 | ❌ `validate_security_config` 无黑名单 |
| CSP 响应头 | ❌ 无 |
| 全局兜底限流 | ❌ 无 `ADSCREEN_GLOBAL_RATE_LIMIT` |
| .gitignore 修补 | ❌ 无 `.env` / `*.db-wal` |
| 22 个核心路径测试 | ⚠️ 存在 13 个测试文件，但非上轮新增项 |
| GitHub Actions CI | ❌ 无 `.github/` |
| 前端 ErrorBoundary | ❌ 无 |

**教训**：方案文档必须标注「计划/已实施」状态，并附验证命令。本轮所有条目均在 §6 给出验证方式。

---

## 五、实施方案

### P0（本轮实施）

1. **重写 `requirements.txt`**：补齐 numpy、pillow、python-dateutil、pydicom、nibabel、scipy、bcrypt；移除已弃用 passlib；torch/monai 以可选注释分组（CPU 演示可不装）。
2. **SQLite 连接级强化**：`journal_mode=WAL`、`busy_timeout=5000`、`foreign_keys=ON`、`synchronous=NORMAL`。
3. **compose 卷对齐**：显式注入 `ADSCREEN_DB_PATH=/app/backend/data/ad_screen.db`。
4. **密钥黑名单**：`validate_security_config` 拒绝已知公开占位密钥（含 compose 示例串）。
5. **生产演示数据开关**：`ADSCREEN_SEED_DEMO_DATA`（dev 默认开 / prod 默认关）；关闭时仅建管理员，密码取 `ADSCREEN_ADMIN_PASSWORD`，未设置则 `secrets` 随机生成并打印一次性日志。

### P1（本轮实施）

6. **CSP 响应头**：`default-src 'self'` + `img-src data: blob:` + `frame-ancestors 'none'` + `object-src 'none'`，保留 Vue/ECharts 所需 inline 豁免。
7. **全局兜底限流**：IP 滑动窗口中间件，默认 600 次/分钟（`ADSCREEN_GLOBAL_RATE_LIMIT`，0=关闭），豁免 health/metrics 与高频 `/imaging/slice`。
8. **GitHub Actions CI**：后端 ruff + pytest；前端 `npm ci` + typecheck + build。
9. **前端 ErrorBoundary**：`onErrorCaptured` 通用组件，包裹 `MainLayout` 的 `router-view`。
10. **.gitignore 补齐** + 清理根目录 `=`。
11. **新增回归测试**：覆盖 WAL、CSP、全局限流、演示数据开关、密钥黑名单、限流豁免。

---

## 五·补、本轮落地结果（实测）

| # | 条目 | 状态 | 落地位置 |
| --- | --- | --- | --- |
| 1 | 依赖清单补全 | ✅ | `ad-screen-backend/requirements.txt` 重写（10 个核心包，移除 passlib）；深度学习拆出 `requirements-ml.txt` |
| 2 | 移除弃用 passlib | ✅ | 同上；测试 `test_deprecated_passlib_removed` 守护 |
| 3 | SQLite WAL/busy_timeout/外键 | ✅ | `database.py` 新增 `_sqlite_pragmas` connect 事件监听；实测 `journal_mode=wal`、`busy_timeout=5000` |
| 4 | compose 卷对齐 | ✅ | `docker-compose.yml` 注入 `ADSCREEN_DB_PATH=/app/backend/data/ad_screen.db` |
| 5 | 占位密钥黑名单 | ✅ | `config.py` `_KNOWN_PLACEHOLDER_KEYS`；compose 示例串实测被拒 |
| 6 | 演示数据显式开关 | ✅ | `config.SEED_DEMO_DATA` + `data_init.py` 改走 flag；实测关闭时仅建管理员并打印随机口令 |
| 7 | CSP 响应头 | ✅ | `main.py` `_CSP_POLICY`；实测响应头含 `default-src 'self'`、`frame-ancestors 'none'`、`object-src 'none'` |
| 8 | 全局兜底限流 | ✅ | `main.py` `global_rate_limit_middleware`；实测阈值 5 时第 6 次请求返回 429，健康检查/切片/OPTIONS 豁免有效 |
| 9 | GitHub Actions CI | ✅ | `.github/workflows/ci.yml`（后端 3.11/3.12 矩阵 + 前端 Node 20） |
| 10 | 前端 ErrorBoundary | ✅ | `src/components/ErrorBoundary.vue` + `MainLayout.vue` 包裹路由出口；`vue-tsc --noEmit` 0 错误 |
| 11 | .gitignore 补齐 | ✅ | 新增 `.env`、`*.db-wal/-shm`、`dist/`、覆盖率产物；删除根目录垃圾文件 `=` |
| 12 | 回归测试 | ✅ | 新增 `tests/test_hardening_round2.py`（24 项），全量 **103 passed** |
| 13 | 遗留未使用导入清理 | ✅ | `ruff --select=F401 --fix` 清理 51 处；`ruff check` 全绿 |

### 验证实测记录

```
ruff check --select=E9,F63,F7,F82,F401,E711,E712 .   → All checks passed!
pytest tests/ -q                                      → 103 passed
vue-tsc --noEmit                                      → 0 error
vite build                                            → ✓ built in 23.63s（2757 模块）
GET /api/health                                       → 200 + CSP 头
PRAGMA journal_mode / busy_timeout                    → wal / 5000
连续 8 次 /api/case/list（阈值 5）                     → 401×4 后 429×4
连续 8 次 /api/health                                  → 200×8（豁免生效）
ADSCREEN_SEED_DEMO_DATA=0                             → 仅建超管 + 随机口令，无演示病例
```

## 六、验证方式

```bash
# 后端测试
cd ad-screen-backend && python -m pytest tests/ -q

# 依赖可安装性（干净环境）
pip install -r ad-screen-backend/requirements.txt && python -c "import numpy,PIL,dateutil,bcrypt,nibabel,pydicom,scipy"

# WAL 生效
python -c "import sqlite3;c=sqlite3.connect('ad_screen.db');print(c.execute('PRAGMA journal_mode').fetchone())"

# CSP / 限流
uvicorn main:app &  curl -I http://localhost:8000/api/health   # 应见 Content-Security-Policy
```

---

## 七、新增环境变量

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `ADSCREEN_SEED_DEMO_DATA` | dev=1 / prod=0 | 是否填充演示账号与病例 |
| `ADSCREEN_ADMIN_PASSWORD` | 随机生成 | 无演示数据时的管理员初始密码 |
| `ADSCREEN_GLOBAL_RATE_LIMIT` | 600 | 每 IP 每分钟请求上限（0=关闭） |
| `ADSCREEN_SQLITE_BUSY_TIMEOUT_MS` | 5000 | SQLite 争锁等待毫秒 |
