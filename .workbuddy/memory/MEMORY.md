# ADScreen 项目长期记忆

## 项目定位
ADScreen · 阿尔兹海默病多模态影像智能筛查与干预平台。
后端 FastAPI + SQLAlchemy + SQLite（`ad-screen-backend/`），前端 Vue3 + TS + Vite（`ad-screen-frontend/`），
算法层为 PyTorch 实现的 TransMF（结构 MRI + PET 融合），训练脚本在仓库根目录。

## 关键约定与事实
- **已是 Git 仓库**（2026-09-27 初始化，分支 main，首次提交 9e11d2c，331 个文件 / 8.4MB）。
  git 身份：loong0012 <1478211871@qq.com>。**尚未配置远端 remote**。
  历史方案文档存在"写了没落地"的情况，任何优化文档必须标注「计划/已实施」并附验证命令。
- 后端规模：31 路由 / 15 服务 / 16 ORM 模型，约 16.6k 行；前端 22 视图 / 120 个 .vue|.ts。
- 数据库为 SQLite，连接级 PRAGMA 在 `database.py` 的 `_sqlite_pragmas` 中下发（WAL + busy_timeout + 外键）。
- tensorflow 级别的深度学习依赖**不在** `requirements.txt`（拆到 `requirements-ml.txt`），
  未安装时后端自动降级为模拟推理，业务功能全可用。
- 测试：`cd ad-screen-backend && python -m pytest tests/ -q`（164 项）。
  静态检查：`ruff check --select=E9,F63,F7,F82,F401,E711,E712 .`
  前端：`npm run test`（vitest，16 项）/ `npm run typecheck` / `npm run build`。
- CI：`.github/workflows/ci.yml`（后端 py3.11/3.12 跑 ruff+pytest，前端 Node 20 跑 vitest+typecheck+build）。
- 测试约定：后端测试**不走 TestClient/httpx**（无 pytest-asyncio），直接调用服务层与中间件函数，
  异步用 `asyncio.run` 包一层，Request 由手工构造的 ASGI scope 建。

## 环境/工具坑
- 隔离 Python：`C:/Users/14782/.workbuddy/binaries/python/envs/default/Scripts/python.exe`
  （系统 python 无 pytest）。
- **冒烟测试必须把 uvicorn 启动与 curl 放在同一条 bash 命令内**，否则后台进程随命令结束被回收，
  后续请求报 502/连接被拒。
- 根目录 `datasets/` 下有大量 .nii 影像（不入库），`checkpoints/` 有模型权重。
- **pytest / vitest 全量跑会被沙箱的批量删除保护拦住**（它们 rmtree os.tmpdir 下的临时目录）。
  解法：跑之前 `export TMP=TEMP=TMPDIR=<一个全新可写目录>`。
  不要给 pytest 传 `--basetemp`（pytest 自身会先 rmtree 该目录，同样被拦）。
- npm 源 `registry.npm.taobao.org` **证书已过期**，安装必须加
  `--registry=https://registry.npmmirror.com`。

## Git 仓库约定
- 忽略策略：源码/文档入库；`datasets/*/`（33G 影像）、checkpoints、best_model_* 权重、
  `*.db`、`.env`、`node_modules/`、`dist/`、`.mimosa/`（AI 会话快照）、`.v2c/` 一律排除。
  `datasets/` 根目录下的 .py（如 ADNI.py）是数据加载代码，**要入库**。
- `.gitattributes` 统一 LF + 二进制标记；Windows 脚本（.bat/.cmd/.ps1）保持 CRLF。
- 提交前自检：`git ls-files | wc -l`（正常 ~331）、`git status` 应干净。

## 已完成的关键能力（四轮优化后）
- 工程可靠性（2026-09-27 第二轮）：依赖清单、SQLite WAL/busy_timeout、CSP、全局限流、
  GitHub Actions CI、前端 ErrorBoundary、.gitignore。测试 103 → 133。
- 互操作层（2026-09-27 第三轮）：FHIR R4 导出（`services/fhir_service.py` + `routers/interop.py`）、
  DICOMweb 只读检索（`services/dicomweb_service.py` + `routers/dicomweb.py`，STOW-RS 未实现返回 501）。
- 批量任务已持久化到 `batch_task_records` 表（原内存单例），多 worker 可共享。
- 容器以非 root（`adscreen` uid 10001）运行，Nginx 监听 8080。
- 合规审计追踪（2026-09-27 第四轮）：`models/audit.py`（AuditEvent + AuditChainHead）、
  `services/audit_policy.py`（声明式范围）、`services/audit_service.py`（哈希链/reseal/purge）、
  `main.py` 审计中间件（最外层，连 401/429 也记）、`routers/audit.py`、
  `routers/interop.py` 的 FHIR AuditEvent 导出。
  **append-only 靠 SQLite 触发器强制**；审计表禁止 UPDATE，所以哈希必须在 INSERT 前算好。
  `audit_board` 是统计看板（旧三张日志表的聚合），与审计追踪不是一回事，别混。

## 未完成的 P2（后续可做）
Alembic 迁移替代手写 `_migrate_columns`（现仅覆盖 2/16 表）；
推理进程独立部署隔离 torch OOM；前端组件级测试与 Playwright E2E、vue-i18n；
STOW-RS 写入 + DICOM SR/SEG 输出；SMART on FHIR + CDS Hooks；
审计日志独立数据库 / WORM 存储（需运维配套）。
