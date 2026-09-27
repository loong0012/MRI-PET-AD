# ADScreen 第四轮优化：合规审计追踪（Audit Trail）

> 日期：2026-09-27　状态：**已实施并通过验证**
> 前序：round2 工程加固（103 tests）、round3 互操作（FHIR R4 + DICOMweb，133 tests）
> 本轮主题：**合规可追溯性**——医疗 AI 系统的监管硬门槛

---

## 一、现状审计结果

对后端 32 个路由文件做埋点统计：

| 指标 | 数值 |
|---|---|
| 写操作端点（POST/PUT/PATCH/DELETE） | **52** |
| 读操作端点（GET） | **101** |
| 审计埋点 | **0** |
| `SystemLog` 写入点 | 1（且仅在 `data_init.py` 演示数据里） |
| `CaseLog` 写入点 | 11（集中在 case / analysis / intervention / report 四个文件） |
| 统一审计事件表 | **不存在** |

结论：系统存在三条彼此孤立的日志表（`system_logs` / `case_logs` / `inference_logs`），
均靠业务代码**手工逐点埋点**——漏埋是必然，且无法回答合规审查的核心问题：

> "谁、在什么时候、从哪个 IP、对**哪位患者**的什么数据、做了什么操作、成功还是失败？"

`audit-board/overview` 目前只是这三条表的**统计聚合看板**，不是审计追踪。

## 二、监管依据（联网核实）

| 来源 | 要求 |
|---|---|
| HIPAA Security Rule 45 CFR §164.312(b) | 对涉及 ePHI 的系统必须实现审计控制，记录并检查活动 |
| HIPAA 保留期 | 审计日志**至少 6 年**（多数机构留存 10 年） |
| 欧盟 GDPR 第 30 条 | 处理活动记录；第 32 条要求处理过程的可追溯性 |
| 中国《个人信息保护法》第 51 条 + 等保 2.0 三级 | 审计记录**至少保存 6 个月**，且不可篡改 |
| FDA SaMD / IEC 62304 | 临床决策类软件需保留模型版本与决策依据可追溯 |
| 等保 / OCR 2026 执法口径 | **书面制度不算证据，要看真实日志** |

行业共识的审计字段集（Accountable HQ / EPC Group / TechAhead 2026 指南高度一致）：

```
用户标识 · 时间戳 · 患者/检查标识 · 动作（view/export/infer/override）
变更前后状态 · 源 IP · 成功/失败码 · 模型版本
```

两条架构要求被反复强调：
1. **append-only + 哈希链**——仅靠访问控制不足以防篡改，需数学证明
2. **审计日志与被审计数据分离存储、独立访问控制**

## 三、改进方案

### P0（本轮实施）

| # | 项 | 做法 |
|---|---|---|
| 1 | 统一审计事件模型 | 新增 `audit_events` 表，覆盖 actor/time/action/resource/patient/outcome/IP/trace_id/model_version |
| 2 | 防篡改哈希链 | 每条记录含 `prev_hash` + `hash`，篡改任一条即断开后续全链 |
| 3 | 数据库级 append-only | SQLite 触发器拒绝 UPDATE/DELETE，绕过应用层也无法改 |
| 4 | 自动审计中间件 | **声明式策略表**，不再依赖手工埋点，杜绝漏埋 |
| 5 | 审计查询接口 | 多维过滤 + 按患者全量访问史 + 完整性校验 + 导出 |
| 6 | FHIR AuditEvent 导出 | 对接上一轮 FHIR 层，供院内 SIEM 采集 |

### 审计范围策略（关键设计决策）

不是"什么都记"，也不是"什么都不记"：

- **所有写操作**（POST/PUT/PATCH/DELETE）→ 一律记录
- **PHI 读操作**（病例/患者/报告/影像/随访/标注等白名单）→ 记录（`read`）
- **数据出域**（导出 CSV/JSON、DICOMweb、FHIR）→ 记录为 `export`（监管重点）
- **高频切片拉取**（`/imaging/slice`）→ 排除，避免单病例上百次请求刷爆审计表

理由：HIPAA 要求的是"对 ePHI 的访问"留痕，而非每一次仪表盘点击；
读操作全记会让审计表在阅片场景下失控增长，反而降低可用性。

### P1（本轮一并实施）

| # | 项 | 做法 |
|---|---|---|
| 7 | 保留策略 | 6 年默认保留，管理员显式触发清理，禁止自动静默删除 |
| 8 | 前端测试基础设施 | vitest + 首个单测，补齐前端 0 测试的空白 |

### 不做（留 P2）

- 审计日志独立数据库 / WORM 存储：需运维配套，超出代码可解决范围
- 密钥托管（KMS）：部署环境问题

## 四、验证方式

```bash
cd ad-screen-backend
python -m pytest tests/ -q                          # 全量回归
python -m ruff check --select=E9,F63,F7,F82,F401,E711,E712 .

# 端到端：中间件自动埋点 + 哈希链校验 + 篡改检测
# 见 tests/test_audit_round4.py 与下方"验证记录"
```

---

## 五、验证记录（实测）

```
后端 ruff     → All checks passed
后端 pytest   → 164 passed（133 + 31 新增）
前端 vitest   → 16 passed（2 个文件；项目此前前端测试数为 0）
```

真实 HTTP 冒烟（单条命令内启动 uvicorn + curl，端口 8140）：

| 检查项 | 结果 |
|---|---|
| 无 token 访问病例列表 | `401`，审计表记 `denied`（actor=匿名） |
| 带 token 读病例 | `200`，审计表记 `read` |
| POST 随访（空体） | `422`，审计表记 `write` + `failure` |
| 哈希链校验（干净库） | `valid=True`，checked=3 |
| 直接 `UPDATE audit_events` | 触发器 ABORT：`audit_events is append-only: UPDATE denied` |
| **摘掉触发器后篡改一条** | `valid=False`，broken_at=AU00000001，reason=「记录内容与哈希不符」 |
| FHIR AuditEvent 导出 | `200`，Bundle total=3，resourceType=AuditEvent |
| 审计列表按 outcome=denied 过滤 | 命中 1 条 |

## 六、实施过程中测试抓出的三个自身缺陷

写测试时暴露了方案本身的漏洞，都已修正——记录在此，因为这类坑复发率高：

1. **「先 INSERT 再回填哈希」与 append-only 触发器直接冲突**
   最初实现是插入记录后再 UPDATE 填 hash，SQLite 触发器立刻 ABORT。
   修正：引入单行链头表 `audit_chain_head`，读链头 → 算哈希 → 插入 → 推进链头，
   全程纯 INSERT。顺带把主键从 O(n) 的「扫全表求 max」改为 O(1) 的零填充序号。

2. **`record_safe()` 把 `SessionLocal()` 放在 try 外面**
   连接池耗尽时这一行就抛异常，会让"写审计失败"演变成"业务请求失败"。
   修正：整个会话生命周期纳入 try/except，审计旁路失败必须静默。

3. **清理超期记录后哈希链断裂，校验误报"被篡改"**
   删除链首会让剩余首条的 prev_hash 悬空。修正：清理后调用 `reseal_chain()`
   从创世值重算整条链（O(n)，仅人工触发的清理后执行，不在请求路径上）。

## 七、遗留（P2，未做）

- 审计日志独立数据库 / WORM 存储：需运维配套，超出代码可解决范围
- Alembic 迁移替代手写 `_migrate_columns`（现仅覆盖 2/16 表）
- 前端组件级测试与 Playwright E2E：本轮只建立了通道并覆盖纯函数
- 推理进程独立部署隔离 torch OOM
