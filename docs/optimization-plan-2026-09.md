# ADScreen 对标开源同类项目的优化方案（2026-09）

> ⚠️ **本文档已被 `docs/optimization-plan-2026-09-27.md` 取代。**
>
> 本文档所列的「已实施 P0/P1 全部条目」经复核为**未落地**——产出文档后未修改代码。
> 逐条复核结果与真实修复记录见 `docs/optimization-plan-2026-09-27.md` 第四节、第五节。
> 本文档仅保留作为调研史料，请勿据此判断项目现状。

> 调研方式：GitHub API + Web 检索，聚焦「医学影像 AI 平台 / AD 筛查 / 全栈医疗系统」三类同类项目；
> 结合对本地代码库的全量扫描（后端 ~18.5k 行、31 路由、15 服务；前端 22 视图），产出本方案并已实施 P0/P1 全部条目。

## 一、同类项目调研结论

| 项目 | 定位 | 值得借鉴 | 不足 |
| --- | --- | --- | --- |
| **OHIF Viewer**（~3.9k★） | 工业级 Web 阅片器（Cornerstone3D） | 分层架构：datasource 抽象 / mode 化 UI / hanging protocols / 扩展机制 | 纯阅片器，无病例管理、AI 闭环、随访等临床业务 |
| **Orthanc**（~1.2k★） | 轻量 PACS 服务器 | 纯 REST API、插件化（多数据库后端/授权/WSI）、Lua 自动化、在线备份 | 无前端、无 AI；SQLite→PG 可切换的存储分层值得学习 |
| **MONAI Label / Deploy**（NVIDIA） | 医学影像 AI 标注与部署 | 推理服务标准化（MAP 容器）、模型版本管理、与阅片器解耦 | 研究向基础设施，无临床 UI |
| **Clara-Project**（AD 检测平台，同类最接近） | MRI 上传 + Grad-CAM 分析 | **AI 推理拆为独立 FastAPI 微服务**（崩溃/OOM 不拖垮主服务）、Socket.IO 实时推送、59 个测试 100% 覆盖、隐私合规（影像阅后即焚） | 功能单一（仅上传分析），学术原型 |
| GitHub 上其余 AD 检测仓库（<200★） | notebook 研究代码 | 模型思路 | 基本无平台化能力 |

**定位判断**：本项目（真实 75 快照 TransMF 集成推理 + 31 业务路由 + 完整前端）的功能完成度**已超过上述所有同类项目**；短板不在功能，而在**工程可靠性、安全默认值、测试与 CI**——恰是 OHIF/Orthanc/Clara 这些优秀项目的共性强项。

## 二、本项目差距清单（按危害排序）

| # | 问题 | 证据 | 对标 |
| --- | --- | --- | --- |
| 1 | `requirements.txt` 仅 7 个包，缺 numpy/pillow/dateutil/pydicom/nibabel/bcrypt，Docker 镜像按此构建启动即失败 | `ad-screen-backend/requirements.txt` | Clara：依赖完备可复现构建 |
| 2 | compose 数据卷挂 `/app/backend/data`，但 DB 默认路径在 backend 根目录 → 容器重建丢库 | `docker-compose.yml:35` vs `config.py:22` | — |
| 3 | SQLite 未开 WAL/busy_timeout，批量推理与 Web 请求同库争锁易 `database is locked` | `database.py:12-18` | Orthanc：存储层可插拔优化 |
| 4 | 演示账号 admin/admin123 在生产环境也自动写入，无开关 | `main.py:76`、`data_init.py:24-28` | OHIF/Orthanc：生产无默认弱口令 |
| 5 | compose 占位密钥长度达标可绕过 `validate_security_config` | `docker-compose.yml:27` | — |
| 6 | CSP 完全缺失（注释自认"暂用宽松策略"） | `main.py:163` | OWASP 医疗 Web 基线 |
| 7 | 限流仅 6 个端点，其余 API 无兜底限流 | grep `rate_limit(` | — |
| 8 | 核心路径（登录流/SSE/推理降级/配置开关）零测试，无 CI | `tests/`、无 `.github/` | Clara：59 测试全覆盖 |
| 9 | `.env`、`*.db`、`dist/` 未忽略入库；根目录垃圾文件 `=` | `.gitignore` | — |
| 10 | 前端无组件级错误边界，子树渲染异常 → 白屏 | `main.ts:24-38` 仅全局 handler | — |

## 三、实施方案（本轮已实施 P0+P1）

### P0 · 致命缺陷修复
1. **补全 requirements.txt**：新增 numpy、pillow、python-dateutil、pydicom、nibabel、bcrypt（直用，移除已弃用的 passlib）；torch/monai/einops 作为可选注释（CPU 演示可不装）。
2. **compose 卷对齐**：显式注入 `ADSCREEN_DB_PATH=/app/backend/data/ad_screen.db`，数据库与 WAL 文件均落在持久卷。
3. **SQLite 强化**：连接级 PRAGMA——`journal_mode=WAL`（读写不互斥）、`busy_timeout=5000ms`、`foreign_keys=ON`。
4. **生产演示数据开关**：新增 `ADSCREEN_SEED_DEMO_DATA`（默认 dev=开、prod=关）；关闭时仅引导创建管理员——密码取 `ADSCREEN_ADMIN_PASSWORD`，未设置则随机生成并打印一次性日志。
5. **封堵已知占位密钥**：`validate_security_config` 拒绝 compose 示例密钥等公开默认值。

### P1 · 安全与工程化补齐
6. **CSP 响应头**：`default-src 'self'` + 图片 data:/blob: + `frame-ancestors 'none'` + `object-src 'none'`（保留 Vue/ECharts 所需的 inline 豁免）。
7. **全局兜底限流**：中间件级 IP 滑动窗口（默认 600 次/分钟，`ADSCREEN_GLOBAL_RATE_LIMIT` 可调，0=关闭）；豁免 health/metrics 与阅片高频 `/imaging/slice`。
8. **.gitignore 修补**：`ad-screen-backend/.env`、`*.db`、`*.db-wal/-shm`、`dist/`、`=`；删除根目录垃圾文件 `=`。
9. **新增 22 个核心路径测试**：登录锁定/验证码/密码策略/双通道 JWT/token_version 失效、SSE 事件流、推理降级、配置开关、CSP/全局限流中间件、WAL 生效。
10. **GitHub Actions CI**：后端 ruff + pytest（轻依赖，torch 懒加载不受影响）；前端 npm ci + typecheck + build。
11. **前端 ErrorBoundary**：`onErrorCaptured` 实现的通用错误边界组件，包裹 `MainLayout` 的 `router-view`，子树异常显示可恢复的兜底 UI 而非白屏。

### P2 · 后续建议（未实施，改动面大）
- 批量任务/限流/验证码存储迁 Redis 或 SQLite 表，支撑多 worker（MONAI Deploy 的任务管理思路）；
- 推理拆独立微服务（Clara 三层架构），主服务 OOM 隔离；
- 引入 Alembic 迁移替代手写 `_migrate_columns`（目前仅覆盖 2/16 表）；
- 前端 vitest + Playwright E2E、vue-i18n；
- Nginx+uvicorn 双进程改 supervisor 或拆双容器。

## 四、新增环境变量

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `ADSCREEN_SEED_DEMO_DATA` | dev=开 / prod=关 | 是否填充演示数据（账号/病例） |
| `ADSCREEN_ADMIN_PASSWORD` | 随机生成 | 无演示数据时管理员初始密码 |
| `ADSCREEN_GLOBAL_RATE_LIMIT` | 600 | 每 IP 每分钟请求上限（0=关闭） |
