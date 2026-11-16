<!-- SPDX-License-Identifier: Apache-2.0 -->

# Auto Ontology：剩余任务、实施计划与 AI Agent 交接书

> 本文面向接手开发的 AI Agent。它与本仓库一起构成执行上下文，不要求访问先前聊天、临时工作区、丢失补丁或原作者的测试环境。
>
> 用户已停止上一位 Agent 的产品开发，要求仅编写交接文档。本文是后续工作的任务书，**不是剩余功能已实现、测试已通过或已经获得生产发布批准的声明**。接手 Agent 收到用户的继续执行指令后，按本文推进。

## 0. 基线、真实进度和阅读顺序

| 项目 | 本次核对结果 |
| --- | --- |
| 文档日期 | 2026-11-16，Asia/Shanghai |
| 目标仓库 | https://github.com/Leafhelpwei/auto-ontology |
| 接手分支 | `fix/audit-hardening` |
| 文档核对的产品代码 SHA | `7cda81020e38d535239f5a5739db75099e878d79` |
| 该提交 | `Preserve clarification constraints and confirm exact governed queries`，2026-11-16 12:29:55 +08:00 |
| 前一批产品提交 | `530bf0330f99a0b6eb33f61ea14e76409522a033`，受限跨事实指标 |
| 原始 60 项审计基线 | `1a5c505dbc3549dc2e80a3a29bd080b9e7ed7b80` |
| 本次读取的 `main` | `fc43a82600e18b19d159c52023f54f5572f537ac`；不含本轮分支全部开发成果 |
| 原始 60 项开发状态 | **44 项已开发 + 1 项已开发受限跨事实 PoC + 5 项开发中 + 10 项未开发** |
| 60 项以外的接续范围 | Auto 自动选路；版本差异与影响分析；业务排除规则闭环；全栈/外部环境验收及文档整理 |
| 远端 CI | 本次查询该分支 Actions runs 返回 `total_count=0`；原因未确认，不能称 CI 已通过 |
| 本次交接编写的核验方式 | 读取固定 SHA 的源码、原审计 JSON、分批验收记录、CI 配置及原规划；没有重新执行产品测试 |

**数字口径：** 45 项“已开发”不等于 45 项在所有环境验收；15 项开发未完成也不包含 Auto 等额外任务。不要将测试用例总数当成审计完成项数，不要相加各批重叠测试形成“全量通过”。完整 60 项映射见附录 A。

最近三项 `LLM-01 / LLM-04 / LLM-05` 已在 `7cda810` 提交，不再按“未提交的临时代码”处理。该批记录是后端 191 项、前端 119 项通过，来源为仓库验收记录；它是历史局部证据，不是本次重新测试的结果。

曾经丢失且未推送的 `7bdc3d4`、`272690b`、补丁及“3275/123/19 项通过”不属于当前可交付基线。本文引用旧轮发现的反例时，只把它们作为**待重新验证的测试设计**，不把旧成果算作已恢复。

### 0.1 先读这几份文件

1. 本文：剩余任务及执行顺序。
2. `AGENTS.md`、`CLAUDE.md`；改前端前另读 `frontend/AGENTS.md`、`frontend/CLAUDE.md`。
3. `docs/audit/REBUILD_STATUS.zh-CN.md`：当前 60 项状态和各批历史验证边界。
4. `docs/audit/original-findings.json`：60 项原始验收条件，保留原编号，不删项或重编号。
5. `docs/plans/auto-ontology-semantic-layer-plan.md`、`docs/plans/adr-001-governed-model-snapshots.md`：原目标和已采用的模型快照决策。
6. `docs/CHAT_RUNTIME.zh-CN.md`、`docs/INTERACTIVE_SQL.zh-CN.md`、`docs/METRIC_NUMERICS.zh-CN.md`、`docs/MULTI_FACT_METRICS.zh-CN.md`、`docs/METRIC_INTERPRETATION.zh-CN.md`：已完成能力的边界。

发生冲突时：用户当前要求优先；判断“已经实现什么”以当前源码和可复现测试为准；原审计定义验收目标；本交接记录剩余差距。历史阶段记录不能覆盖后续修复，也不能替代测试。根目录维护文档有旧 schema 描述，实际归属见第 2 节。

### 0.2 可直接交给下一位 Agent 的执行指令

> 继续 `Leafhelpwei/auto-ontology` 的 `fix/audit-hardening` 分支。先完整阅读 `docs/audit/AGENT_HANDOFF.zh-CN.md`，记录实际 HEAD 和工作区状态，对照 `7cda81020e38d535239f5a5739db75099e878d79` 核验增量。完成其中所有未完成开发及可独立执行的验证，并保留全部 60 项编号。复用当前已完成的修复；按里程碑实现、测试、记录证据和提交。重点完成默认 Auto 的确定性查询优先、明确能力不足时探索、权限/认证/执行故障绝不触发降级。每批完成后继续下一批，不以“已推送”代替任务完成。环境阻塞时明确记录条件和补验步骤，继续其他独立任务；不得虚报外部验收。不得回退到 main、覆盖未知改动、自动认证真实业务口径、连接生产库做破坏性测试或擅自生产发布。

## 1. 接手第一轮：S0 基线与执行准备

### 1.1 获取正确代码

全新工作区：

```bash
git clone --branch fix/audit-hardening https://github.com/Leafhelpwei/auto-ontology.git
cd auto-ontology
git status --short
git rev-parse HEAD
git log -5 --oneline
```

已有仓库：先检查工作区与远端，再 `git fetch origin`。存在未提交改动时先辨认归属，不能 `reset --hard` 或清理它们。文档提交会使 HEAD 晚于产品基线，属于正常情况。

```bash
git status --short
git branch --show-current
git remote -v
git fetch origin
git merge-base --is-ancestor 7cda81020e38d535239f5a5739db75099e878d79 HEAD
git diff --stat 7cda81020e38d535239f5a5739db75099e878d79..HEAD
```

若基线不是祖先，先排除浅克隆、错误分支或历史变更。不要仅凭“最新 main”继续。仓库规范“从 main 分支”是通用规则；本任务明确接续已有修复分支，应保留现有提交，可从其最新 HEAD 新建接续分支。

### 1.2 开工产物

- [ ] 建立当前执行记录，逐项使用 `未开发 / 开发中 / 已开发待验收 / 本地验收通过 / 外部验收受阻 / 完整验收通过`；开发和验收分别记录。
- [ ] 记录 HEAD、操作系统、Python/Node/数据库版本、锁文件摘要、测试环境及基线失败。
- [ ] 核对本文每个剩余项在最新 HEAD 是否已经有后来提交；有代码时先验证，不重复实现。
- [ ] 准备隔离 PostgreSQL、MySQL 5.7.44、前端 Auth schema 和合成夹具。
- [ ] 确认 GitHub Actions 是否启用、是否等待 fork 工作流授权、触发过滤和所需检查状态；没有运行记录不等于绿色。
- [ ] 确立迁移/schema/OpenAPI 的单一整合负责人，避免多条 Alembic head 和不一致 DTO。
- [ ] 为每批设置明确 DoD：实现、反例测试、契约/迁移更新、文档、提交 SHA、远端核对；然后继续下一批。

## 2. 项目结构、既有能力及必须保留的边界

### 2.1 实际技术栈与代码入口

| 内容 | 入口 / 规则 |
| --- | --- |
| 后端 | Python 3.12，FastAPI，SQLAlchemy Core，Pydantic，Alembic，`uv.lock` |
| 前端 | Next.js 16、React、TypeScript strict、Tailwind CSS 4、pnpm 10.34.3、Vitest、Playwright；不要改成 Vue 或另建前端 |
| 服务启动 | `auto_ontology/server/__main__.py:create_app`；不存在 `server/main.py` |
| Chat 请求 DTO | `auto_ontology/server/chat/helpers.py` 的 `ChatRequest`、`MetricChatContext` |
| Chat 调度 / 生命周期 | `server/chat/router.py`、`worker.py`、`run_lifecycle.py`；共享运行记录在 `dal/chat_runs.py` |
| 受治理 Chat | `server/chat/metrics_flow.py`，解释在 `server/metrics/interpretation.py`、`interpretation_view.py` |
| 指标契约 / 编译 | `semantic/contracts/models.py`、`validator.py`、`compiler.py` |
| 治理 / 真实执行 | `server/metrics/service.py`、`source_adapters.py`；持久化 `dal/governed_models.py` |
| 前端 Chat | `frontend/lib/useChat.ts`、`frontend/api/chat.ts`、`frontend/types/chat.ts`、`components/chatPage/` |
| 前端治理 | `frontend/components/metricsPage/`、`frontend/lib/modelDrafts.ts` |
| 公开权限入口 | Next.js `/api`、`frontend/auth/with-auth.ts`、`proxy-backend.ts`、`lib/internalIdentity.ts`；FastAPI 仍是受信内部跳转 |
| 摄取 / 向量 | `ingestion_service/`、`catalog/ingest.py`、`semantic/`、`vdb/postgres.py` |
| 模型协议 | `utils/chat_protocol.py`、`utils/llm_invoke.py`、`utils/model_config.py` |
| 结果预算 / 只读 | `connectors/query_policy.py`、`connectors/base.py` 及各驱动；探索执行/探测仍须共用边界 |

上述省略前缀的 Python 路径均相对 `auto_ontology/`。本文件后文标为“建议新增”的文件/API 尚不存在，不得宣称已可调用。

**数据库归属：** 当前 Alembic / DAL 管 `public`；Prisma 管 `frontend`；向量管 `vdb`。依据 `alembic/env.py`、`auto_ontology/dal/session.py`、`frontend/prisma.config.ts`。维护文档和 Makefile 部分注释仍写旧归属，不能据此让迁移跨 schema 删表。内部元数据仍需 PostgreSQL + pgvector；MySQL 是业务数据源，不能拿它替换元数据库。

### 2.2 已完成、应复用的能力

- 统一首部 System 消息、用户证据保持数据角色、显式模型/端点配置；集中调用预算最多 3 次，SDK 重试不叠乘。永久认证/配置/拒答错误不能跨端点重试；跨端点需明确 allowlist。
- SSO JWT 签名、issuer/audience/有效期、本地封禁和 provider+subject 绑定；连接字段白名单、TLS、秘密字段原文保留和显式清空。
- Chat request ID、工作进程淘汰、启动/执行截止时间、配额、持久租约、有序 SSE 重放、跨副本取消、连接 generation；不能被 Auto 改回进程内状态。
- 探索 SQL AST 检查、数据库只读边界；PG/MySQL 的交互结果行/字节预算、超大单行保护、取消清理和截断历史提示。
- 指标完整模型不可变快照、版本与认证、来源指纹、只验证查询实际依赖、角色路径、精确数值、MySQL 5.7 半开时间区间。
- 受限跨事实：同一受支持数据库/快照中先分别聚合，再按获批公共维度对齐；支持明确原子时间角色。不是任意事实 JOIN、跨连接或预聚合能力。
- 澄清链超窗要求重述；`confirmation_required` 后通过 `confirm_query_fingerprint` 执行已展示的请求；确认时不再调用模型改写；`trusted` 与 `intent_confirmed` 分别表达契约执行和意图确认。
- 前端草稿恢复、并发冲突、稳定 ID/引用检查、分页、Catalog 选择器、图谱累计预算及键盘列表等基础已存在。

### 2.3 不可破坏的约束

1. 认证模型使用固定 revision；新草稿不能覆盖有效旧版，导入 certified 字段不能获得认证。
2. 用户确认必须绑定用户、会话、最新待执行提案、模型版本和查询指纹，不能用正文“确认了”模拟授权状态。
3. 权限、认证、结构漂移、连接 TLS、数据库执行或模型调用错误必须作为错误处理，不能通过探索 SQL 绕过。
4. 业务时间、币种、排除条件、角色路径不得静默丢失；缺少条件先澄清或要求重述。
5. Decimal/bigint 保留精确字符串；非有限数显式拒绝；不能把 `CONFIRMED` 当 `PAID`，不能混合币种求和。
6. SQL 只读和结果预算作用于全部执行及探测入口；普通管理/摄取接口的写能力不能被 Chat 借用。
7. `zone` 和目录 owner 目前不是业务数据安全边界；会话归属隔离不等于租户数据隔离。
8. 示例、模型替身、本地 HTTP、真实供应商、目标设备和生产验收分开记录。
9. 模型快照是已采用 ADR；指标级独立发布、跨模型依赖属于后续扩展，不能为此次交接擅自重写全部版本系统。

## 3. 实施路线与批次计划

按完成条件推进，不承诺未经测量的工期。可以在缺外部账号时继续本地独立工作；不能因一次推送或一个受阻项结束整个任务。

| 里程碑 | 工作包 | 主要依赖 | 离开本阶段的条件 |
| --- | --- | --- | --- |
| M0 接手 | S0、基线回归、远端 CI 排障 | 最新分支 | 版本/环境/当前差距可复现 |
| M1 基础门禁与诊断 | ENG-02、ENG-09、LLM-06、RUNTIME-10、VAL-09 | M0 | 单次测试报告、日志不含原文、异步不被慢库阻塞；资源警告定位 |
| M2 无损升级 | ENG-01 | M0 | 旧库保留/归档、Prisma 版本化迁移、幂等及回退演练 |
| M3 摄取可靠性 | RUNTIME-07 → RUNTIME-08 → RUNTIME-09 | M0；新增迁移规则明确 | 双副本、kill、租约失效、完整旧索引/原子切换全部实测 |
| M4 数据边界 | SEC-08 的信任域/主体策略 → SEC-07 | M2/M3 的迁移与清理能力 | 查询/检索/采样/日志/导出一致执行权限与脱敏 |
| M5 查询与治理闭环 | AUTO-01、PLAN-01、PLAN-02 | 复用现有 Chat；遵守 M4 策略接口 | Auto 端到端、确认/续问完整、版本差异与排除规则可验收 |
| M6 交付与容量 | ENG-05 → ENG-04；RUNTIME-05；ENG-06 | M1；跟随 M3/M5 事件接口 | 驱动边界、锁文件构建、导出和观测有实测证据 |
| M7 部署恢复与验收 | ENG-07、VAL-01…VAL-08、DOC-01 | 相关实现稳定 | 恢复/切换、浏览器、可用外部环境、单次全量 CI 均按项记录 |

M3 与 M5 的内部开发可独立安排，但合并前必须统一身份、查询状态与 schema；M4 未完成时 M5 不能声称满足租户/PII 验收。ENG-06 复用现有 request/run ID，不需等待另造追踪系统。

每批提交应包含：实现 + 失败反例/集成测试 + 必要迁移/API + 使用说明 + `REBUILD_STATUS.zh-CN.md` 更新。原来已经授权向用户 fork 交付，不要把一批推送当作总任务终点；禁止自行合并 main、对 NVIDIA 上游开 PR 或发布生产。若接手环境没有写权限，提供可应用提交/补丁及阻塞原因，同时保存已完成工作。

## 4. 60 项中仍有开发工作的 15 项

本节的“原优先级”来自审计；执行先后由第 3 节依赖决定。测试文件标为“建议新增”时需要实际编写，不能仅在文档中列名就算覆盖。

### RUNTIME-05｜补全驱动结果预算和独立导出（原 P1，开发中）

**当前：** PG/MySQL 已有真实大结果/RSS/取消回归。默认交互预算为 1000 行、512 KiB 总序列化量、64 KiB 单行、128 列；不能恢复到 `fetchall → DataFrame → JSON`。其他仓库驱动的分配前内存保证和大结果导出尚未完成。

**入口：** `connectors/base.py`、`query_policy.py`、各驱动、`retrieval/text_to_sql/agents/sql_execution.py`、`db_probe/executor.py`、`server/chat/router.py`。既有测试：`connectors/tests/test_bounded_reads_live.py`、`test_query_policy.py`、`server/chat/tests/test_pump_sql_events.py`。

**实施：**

- [ ] 逐一梳理 PostgreSQL、MySQL、SQLite、DuckDB、Databricks、Snowflake、ClickHouse、Kyuubi、Trino、HeavyDB 的驱动缓冲、分批协议、单行上限、取消和只读能力；形成实际能力表。
- [ ] 为交互路径使用真正流式/分批传输。普通客户端 `fetchmany()` 若底层已全量缓冲，不算完成。无法安全限制的路径明确拒绝受保护交互能力并保留不支持原因，不能宣称与 PG/MySQL 等价；需要支持的驱动仍须补实现和实测。
- [ ] 导出建立独立、有权限和租约的流式任务，设置行/字节/时间/并发/保留期，绑定查询、版本、主体和策略；下载时再次鉴权，取消或过期清理临时产物。SSE 只携带摘要/引用，不内嵌整个文件。
- [ ] 保留截断原因、返回行列信息、精确值、空结果、重复列名；部分结果不得被模型解释成全集。导出截断必须可见，不得伪装完整下载。

**验收：** 小结果与原行为一致；百万行及 64 MiB 单元格时受控 RSS；冷启动与查询阶段单独测量；并发查询、取消、超时和进程杀死后服务端查询/事务释放；超限元数据刷新后仍在。PG/MySQL 已有 32 MiB 查询阶段增量回归门槛可保留，其他引擎应根据隔离进程/配置记录明确预算，不能只测 Python 返回后的大小。导出覆盖越权、过期、取消、精确数值及预算耗尽。没有真实引擎环境的项标记外部受阻。

### RUNTIME-07｜持久摄取任务与恢复（原 P2，未开发）

**当前：** `ingestion_service/ingest.py` 的手动入口仍启动 daemon Thread；router 返回 `{"status":"accepted"}`，无持久 job_id；运行/停止/失联的历史语义不完整。

**入口：** `ingestion_service/router.py`、`ingest.py`、`history.py`、`data_scheduler.py`、`semantic_scheduler.py`、`server/ingestion/proxy.py`、`dal/schema.py`。可参考 Chat 的 `dal/chat_runs.py`、`server/chat/run_lifecycle.py`，但不要混用摄取与 Chat 的配额/生命周期。

**实施：**

- [ ] 用现有 PostgreSQL 增量表实现 durable jobs/outbox：job_id、可信作用域/source_id、类型、幂等键、状态、阶段、attempt、next_retry_at、owner、lease_until、fencing token、心跳、取消标记、脱敏错误摘要与时间戳。
- [ ] **事务提交任务后才返回 202**，返回可查询 job_id；状态查询、取消和人工重试走现有公开权限代理。
- [ ] 状态至少区分 queued/running/retry_wait/succeeded/failed/cancelled/interrupted。不可达是服务观测状态，不是空闲或作业成功。
- [ ] 持久领取、租约续期、有界退避及启动时孤儿任务处理；阶段幂等。只有可证明安全的阶段自动恢复，其余明确 interrupted 并允许受控重试。
- [ ] 改造 UI/代理，区分 idle、running、unreachable、failed、interrupted；旧历史 NULL 不猜测为成功。
- [ ] 子进程隔离/回收覆盖停止、超时、父进程 SIGKILL；与 RUNTIME-08 的写入围栏共用身份。

**验收：** 202 之后立即 kill 并重启仍能按 job_id 找到任务；要么安全恢复，要么明确中断；重复提交/重试不重复发布；运行、取消、失败不混淆；数据库持久化失败不能返回 202。测试必须有真实数据库和实际进程死亡，不仅 mock 状态字段。

### RUNTIME-08｜摄取、编译、删除与 reset 的共享互斥（原 P1，未开发）

**当前：** scheduler 的 running/pause/abort 主要是本进程状态；手工摄取/删除独立线程，刷新 connector 可能关闭其他任务正在用的连接。

**入口：** `ingestion_service/__main__.py`、`scheduler.py`、`ingest.py`、`connections.py`、`connectors/registry.py`、`catalog/ingest.py`、`dal/session.py`、Helm ingestion 模板。

**实施：**

- [ ] 基于 RUNTIME-07，以可信隔离作用域 + 稳定数据源 ID 建立共享互斥；摄取/语义编译/delete/reset 都参与同一冲突矩阵。
- [ ] 同源重复请求按幂等键合并或返回明确冲突；不同源在全局限额内可以并行。全库 reset/delete 必须与各源任务协调。
- [ ] owner lease 失效即禁止后续写入与发布。**不仅父进程监测**：Catalog、向量和 active-generation 切换等写事务在开始及提交前检查 ownership/fence；过期 worker 即使恢复也不能污染新任务。
- [ ] connector 使用任务级生命周期或安全引用管理；轮换缓存不能关闭别的活动任务；删除 source 后旧任务不能复活它。
- [ ] 定时器只提交/领取共享任务；两个副本启动不能双重执行同一 schedule occurrence。

**验收：** 两个实际服务进程同时 ingest 同源、ingest+delete、compile+reset，观察合法串行顺序；不同源并行；续约暂停/过期后旧 worker 的 Catalog 事务回滚；SIGKILL 后租约可回收、无遗留子进程；连接轮换不影响其他合法作业。仅设置 `replicas: 1` 不满足本项。

### RUNTIME-09｜向量索引 generation 原子发布（原 P1，未开发）

**当前：** `run_ingest` 首个成功 chunk 调用 `get_data_vdb(..., reset=True)`，随后逐块写线上索引；后续异常或 None embedding 可留下不完整新索引。

**入口：** `ingestion_service/ingest.py`、`semantic/embed.py`、`server/model_interchange/embed.py`、`vdb/postgres.py`、`vdb/records.py`；既有 `ingestion_service/tests/test_ingest_reset.py`、`test_semantic_reset_order.py`、`vdb/tests/test_vector_index.py`。

**实施：**

- [ ] 数据源/索引类型/隔离作用域拥有 immutable generation，staging 与 active 分离；检索、重排和所有重建入口统一读取 active。
- [ ] 发布前验证期望记录集合与成功集合、embedding 数量/维度、重复 ID、错误/空 embedding、当前 Catalog/策略版本；不能用“至少一块成功”当门槛。
- [ ] 全量成功后在事务内比较 fencing token、来源/策略版本并原子切换 active 指针；失败保留完整旧 active。
- [ ] 首次空数据源、合法变空、部分失败分别处理。旧版本与废弃 staging 异步清理，清理不得删除当前 active 或仍被读者引用的数据。
- [ ] 更新现有允许“a/c 失败但 b 发布成功”的旧测试预期，保留失败原因并使历史记录与发布状态一致。

**验收：** 第二块抛错、部分 embedding=None、写入中 kill、切换前后 kill、旧 owner 重试均不能出现部分/混合 generation；成功一次切换到完整新集合；双源/双租户不互相清索引；GC/恢复有真实 PostgreSQL 测试。PII 策略变化导致旧敏感 generation 禁用时，应优先安全失败，不能继续暴露它等待重建。

### RUNTIME-10｜异步路由中的阻塞数据库调用（原 P2，开发中）

**当前：** Chat 的部分同步调用已移到线程；`ingestion_service/router.py` 的 async health/semantic_status 仍直接调用同步数据库 helper。

**入口：** `ingestion_service/router.py`、`history.py`、`server/health/`、`server/chat/router.py`、其他 async route 及依赖调用链。

**实施：**

- [ ] 对无异步 I/O 路由使用 FastAPI 同步 def；混合路由用显式有界 executor/to_thread 包装同步段。检查 helper 的下游 SQL/连接池等待，不只检查函数声明。
- [ ] 连接、池等待、SQL/锁等待分别有截止时间；liveness 不依赖慢数据库，readiness 的数据库检查有短预算。
- [ ] 线程池饱和可观测且有背压；线程移出事件循环不代表数据库查询可以无限运行，取消/超时仍须收尾。

**验收：** 注入 300 ms 同步 DB 等待时，10 ms ticker、并发 liveness、取消入口保持约定延迟；真实长锁等待在预算内结束；并发耗尽池时服务可响应。记录机器/阈值和 p95，不以简单 `await` 包装通过源码检查充当验收。

### SEC-07｜从 PII 分类到采样、提示、结果策略（原 P2，未开发）

**当前：** PII 标签不等于脱敏；采样存储、embedding、LLM 提示和指标结果尚无统一策略。`pii:read/manage` 是设置权限，不能自行当作读取全部敏感值的授权。

**入口：** `pii_detection/`、`dal/pii.py`、`semantic/visit_enter.py`、`semantic/embed.py`、`retrieval/text_to_sql/formatters_util.py`、SQL 校验/执行/探测、`server/metrics/service.py`、模型解释视图、导出、历史和日志。

**实施：**

- [ ] 与 SEC-08 共用可信主体/数据源/列策略；明确 raw-read、masked-read、aggregate 等授权语义及默认拒绝行为，不信任客户端传 pii_allow。
- [ ] 在采样进入 Catalog 前、embedding 前、LLM 提示前、查询规划/投影和输出前分别执行。外部 LLM/embedding 的允许范围独立于用户是否能看原始值。
- [ ] SQL 列依赖解析覆盖 `*`、`table.*`、整行/复合行、CTE/子查询、别名、聚合、表达式、JOIN USING/NATURAL JOIN 的隐式列；无法证明安全的依赖拒绝。禁止依靠输出列名匹配标签。
- [ ] 过滤/排序/分组和聚合也纳入策略，不能只遮最终单元格而任意允许敏感列侧信道查询；支持哪些安全汇总必须明确配置。
- [ ] 处理历史 Catalog 样本、索引、提示/结果缓存和持久历史的清理/重建；策略撤销后旧缓存/会话下载不能继续授权。清理记录只含 ID/数量等诊断信息。
- [ ] UI/文档明确“分类开关不自动脱敏”；业务查询仍可通过获批脱敏视图/最小权限账号实施更强数据库边界。

**验收：** 使用合成邮箱/手机号/密钥哨兵，无授权时样本库、embedding 请求、LLM 请求、结果、错误、SSE、导出、日志与缓存均不含原文；不同授权主体/获批汇总正常；通配符/整行/隐式 JOIN 反例均覆盖；历史敏感索引可撤销并重建。测试替身验证出站内容后，再在授权沙箱做供应商实测，不发送真实 PII。

### SEC-08｜明确并落实租户/行列隔离（原 P2，未开发）

**当前：** 这是全局共享目录与数据源设计的能力缺口，不能称已证实的任意越权漏洞。`zone_ids`、owner、模型认证和 Chat 会话所有权均不能当作业务数据隔离。

**范围决策：** 原审计允许“共享实例策略”或“每租户独立实例/数据库凭据”。本交接建议先落实可交付的独立实例方案，保留现有全局共享模式并明确适用信任域。如果产品要求一个实例内不互信租户，则必须完成下面共享实例全部链路，不能拿独立实例测试冒充共享实例多租户实现。

**实施 A：独立实例的受支持部署能力。**

- [ ] 每信任域独立元数据库/凭据、业务数据账号或隔离 schema、认证配置、缓存/文件/向量 namespace；提供配置模板及启动校验，禁止不安全地复用跨租户默认连接。
- [ ] PostgreSQL 共用业务表时用非 owner、无 BYPASSRLS 应用角色及适当 FORCE RLS/策略兜底；MySQL 5.7 没有 PostgreSQL 式原生 RLS，应使用独立库/账号或严格受控视图，不能写成“已启用 MySQL RLS”。
- [ ] 导出、恢复、备份和运行任务均绑定对应实例/信任域；恢复不能覆盖另一租户。

**实施 B：如果选择共享实例。**

- [ ] 将主体/组到 datasource/model/行列的策略注入列表、详情、搜索、检索、编译、执行、导出、历史、任务及缓存键；API 中不能信任任意 tenant/zone/model ID。
- [ ] DAL/RLS、唯一约束、worker pool、connector、generation 和认证依赖都带可信作用域；连接池每次事务设置并清理主体上下文，防止复用污染。
- [ ] 保留明确配置的全局共享模式和旧用户迁移说明；以新迁移引入约束，不能静默改变全部可见性。

**两方案共同验收：** 双用户/双租户同名对象贯穿列表、详情、搜索、导出、Chat、compile/execute、历史与缓存；参数篡改不可扩大范围；实际数据库隔离在应用漏过滤时仍有效；身份/连接复用和重连不跨界。分别报告应用实例隔离、共享实例授权、数据库兜底做到了哪一层。部署图或文档说明本身不算实现完成。

### LLM-06｜清理原文日志和异常泄露（原 P2，开发中）

**当前：** Chat 入口已改记录模式/问题长度，SQL 有指纹日志；探索入口、intent、worker、重试和异常链路仍须完整复核。

**入口：** `server/chat/router.py`、`worker.py`、`retrieval/text_to_sql/main.py`、`agents/question_intent.py`、`utils/llm_invoke.py`、连接器和前端代理错误处理。

- [ ] 默认只记 request/run/conversation ID、阶段、错误类/稳定错误码、长度、时延、计数；问题、完整 SQL、参数、token、DSN、provider body 不直接输出。
- [ ] 对 `logger.exception`、traceback、重试、调试、stdout/stderr 和第三方 HTTP 日志统一处理，避免“业务 logger 安全、SDK logger 泄漏”。
- [ ] 内容诊断如保留需显式 opt-in、最小权限、脱敏及保留期；默认关闭。不能把明文转移到 trace attribute 规避本项。
- [ ] 与 ENG-06 共用结构化事件，保留可诊断关联，不输出 secret 的可逆编码。

**验收：** caplog/子进程日志/HTTP 错误捕获包含 Chat、explore、intent、provider 400/401/429/5xx、SQL 异常和取消，所有合成敏感哨兵均不出现；仍可按同一 run_id 关联故障。

### ENG-01｜旧库无损升级与 Prisma 迁移链（原 P1，未开发）

**当前：** `frontend/prisma/migrate.sh` 遇缺 user_id 的旧 `frontend.conversation_analytics` 会 TRUNCATE；package 的 start/dev 仍 db push。仅新库升级通过无法证明旧库安全。

**入口：** `frontend/prisma/migrate.sh`、`schema.prisma`、`prisma.config.ts`、`package.json`、`frontend/Dockerfile`、Helm frontend-migrate job、CI 初始化。

- [ ] 建立版本化 Prisma baseline 与增量 migration，区分新安装、已存在 schema、旧 schema；baseline resolve 必须先验证真实结构匹配，不能直接把未知库标为已应用。
- [ ] 能可靠绑定用户的历史记录可审计回填；无法归属的记录迁移到受限归档，保留原 ID/时间/内容及原因，不分配给任意 admin/当前用户。
- [ ] 使用扩展→回填→校验→约束收紧的升级过程，保留兼容窗口；生产使用 `prisma migrate deploy`，迁移与应用启动解耦。
- [ ] 移除 TRUNCATE 和用 `--accept-data-loss` 放行的路径；同步镜像 migration stage、Helm 和 CI，不能只改 shell 而保留另一条破坏入口。
- [ ] 编写升级前备份、失败中止、前滚修复/恢复方案，覆盖 public/frontend/vdb 的 ownership；不修改已应用 Alembic migration。

**验收：** 两条以上旧历史（含可归属/不可归属）升级后总保留量与字段正确；Auth、会话、指标/向量可用；重复执行幂等；中途失败恢复；新装与现有新版本均通过；实际恢复演练后给出记录和查询证据。保留归档必须有可查询/恢复说明，不算删除后写一句“已备份”。

### ENG-02｜开发分支和 PR 的完整 CI 门禁（原 P2，开发中）

**当前：** Client CI push 已包含 `fix/**`，Python/OpenAPI 等仍主要 main/PR；本次该分支没有任何 Actions run 记录。触发配置、平台启用与实际结果需分别查明。

- [ ] 核对 `.github/workflows/ci-python.yml`、`ci-client.yml`、`ci-openapi.yml`、`ci-mcp.yml` 的分支/路径/workflow_dispatch；实际接续分支必须触发所需检查。
- [ ] 检查 fork Actions 启用/等待授权、组织策略、runner、action allowlist、所需凭据；不要将“无 run”自动归因于某一种原因。
- [ ] 保留完整 Python+真实数据库、前端 Vitest/类型/构建/浏览器、OpenAPI 与 MCP 的门禁。必要时增加总汇 gate，避免 path-filter 让 required check 永久 pending 或未运行却被当成功。
- [ ] 补齐真实 MySQL TLS fixture：当前 workflow 未设置 `MYSQL_WRONG_CA`，而 `test_mysql_tls_live.py` 缺它就跳过 4 个测试；已有两个 MySQL DSN 也没有明确 CA 配置。生成可信/不可信两套测试 CA，服务端证书 SAN 包含 `127.0.0.1` 且排除 `localhost`，应用 reader 要求 SSL，传入 `ssl_mode=VERIFY_IDENTITY&ssl_ca=...`。不得把这些必跑测试加入 skip 白名单来修绿 CI。
- [ ] 在用户 fork 创建可审查 PR 并核对 required checks；若设置 branch protection 需仓库管理员权限，记录具体缺口，不绕过保护或伪称已生效。

**验收：** 对具体 HEAD 可给出 run URL、job 结论及 artifact；故意失败的测试确实让检查失败且阻止合并；修复后同一候选 HEAD 全部必要检查通过。只开 workflow、只推代码或只有本地结果不能关闭此项。

### ENG-04｜镜像遵守锁文件（原 P2，开发中）

**当前：** 移除 Google Fonts 构建下载已完成；后端 Dockerfile 仍 `uv sync` 未 locked，前端迁移 stage 有 `npm install --no-package-lock` 等漂移点。

- [ ] 后端构建使用可验证的 `uv sync --locked`；同步 ENG-05 的 extras 与锁文件，不能用修改锁文件“自动修复”声明漂移。
- [ ] 前端 migration 工具使用已提交且审查过的独立锁文件或锁定 pnpm 部署产物，干净环境 `npm ci`/等价冻结安装；审查临时安装和 `corepack@latest` 等未固定工具链。
- [ ] 检查根、frontend、mcp Dockerfile 及发布 workflow 的 Python/Node/工具和依赖来源；记录 base image 标识/SBOM/实际依赖清单。
- [ ] 对同一 commit 做两次干净构建并比较依赖清单；镜像二进制受时间戳影响不一定逐字节一致，应明确比较对象。

**验收：** 人为使 manifest 与 lock 不一致时构建失败；正常两次构建依赖一致；migration stage 启动并完成隔离库迁移；无真实数据库/外部字体依赖的前端构建通过；不需要生产发布才验证镜像。

### ENG-05｜按需连接器依赖和惰性加载（原 P2，未开发）

**入口：** `pyproject.toml`、`uv.lock`、`connectors/__init__.py`、`registry.py`、各驱动、`mcp/pyproject.toml` 及镜像/CI。

- [ ] 用 `uv add --optional` 等仓库规定方式拆分连接器 extras，保留核心 PostgreSQL 元数据库能力；定义常用 postgres/mysql 安装组合和兼容的全驱动组合。
- [ ] 采用惰性工厂/明确注册元数据，不在 package import 或类型导入时加载全部驱动。保持既有公开导入/API 的兼容或提供明确迁移。
- [ ] 选择未安装驱动时返回可操作安装说明；不能让一个可选 SDK import 错误阻止整个服务/OpenAPI/健康检查启动。
- [ ] 在隔离 venv/镜像测试仅核心、核心+mysql、各目标 extra、Kumo 和全量组合；不要在已安装全部依赖的环境里测试“可选依赖缺失”。

**验收：** 核心+mysql 能启动、建模、查询；未装 Snowflake/Databricks 等不影响核心；选择缺失驱动明确报错；记录前后安装体积、镜像体积、冷启动和 import 耗时，不预先声称收益。

### ENG-06｜跨网关、worker、LLM、SQL 的观测与预算（原 P2，未开发）

**当前：** 已有 request/run ID、共享运行记录及部分时延日志；缺少贯穿各阶段的统一 trace、用量、成本与告警，不应重新造一套不相容 ID。

- [ ] 在 Next/FastAPI → 调度/队列 → IPC worker → 检索/解释/编译 → LLM/SQL → 持久化/SSE 传递 trace 上下文；复用 run_id，外来 ID 做校验，认证身份只从可信入口获得。
- [ ] 使用 OpenTelemetry 或兼容结构化方案记录阶段时长、排队、重试、取消、异常类、worker/连接池和任务租约指标；控制标签基数，不把用户问题/SQL/敏感 ID 作为指标 label。
- [ ] 记录供应商、模型/版本、input/output/cached/reasoning token（可用才记）、实际尝试次数；供应商未返回 usage 记 unknown，不能写 0 或伪造精确值。
- [ ] 费用以版本化价目配置估算并标明估算；配置请求/租户预算和并发上限，不为采集自动启用有费用的外部平台。
- [ ] 定义 p50/p95、错误率、取消延迟、队列/租约失效、调用预算告警和运行手册；trace 采用 LLM-06 的默认不采集内容策略。

**验收：** 一次正常/重试/失败/取消请求可从网关追到 SQL；双进程和重连后 ID 一致；能按 run 解释 token 和费用来源；注入超预算触发告警/拒绝；日志和 trace 均无敏感哨兵。真实供应商用量与本地 mock 分开报告。

### ENG-07｜外部 PostgreSQL、备份恢复和故障切换（原 P1，未开发）

**当前：** 随附数据库为单副本单卷；PVC 不是备份。已有外部连接配置需核对完整性，不能盲目重复添加同义配置。

- [ ] 明确开发内置库与生产 externalDatabase 方案，支持已有托管 PG 或经运维验证的 operator；连接信息经 Secret 注入，明确 TLS、扩展和三类 schema 的权限。
- [ ] 定义备份范围、保留期、加密、WAL/PITR、恢复流程和 RPO/RTO 目标，覆盖认证模型/证据、Auth、会话、运行任务、Catalog、向量 generation 及相应密钥/配置依赖。
- [ ] 在新隔离实例恢复，不覆盖原库；恢复后 revalidate 连接、来源指纹和权限，恢复历史证据不能自动认证未知来源。
- [ ] 用真实主备/托管测试环境执行故障切换；处理连接池重连、在途事务、租约、重复任务和已完成消息。不能把副本数改为 2 当成数据库复制。
- [ ] 记录实测 RPO/RTO、数据损失/重复边界、停机与回滚条件，给出运维命令/配置和演练记录。

**验收：** 恢复后模型认证证据、用户会话、向量检索、实际查询及 job 状态可读且一致；备份损坏/缺文件能检测；故障切换后无旧 owner 越权提交、无重复发布。没有目标部署权限时交付本地可跑演练和配置，将生产环境验证单独列受阻，不宣称生产 HA 已验收。

### ENG-09｜一次 pytest 报告驱动全部 CI 统计（原 P3，未开发）

**当前：** Python workflow 主测试后为统计 skip 再跑整套，异常时可能第三次。

- [ ] 单次 pytest 输出 JUnit XML/受支持结构化报告，失败计数、skip guard、摘要、artifact 全读这一份。
- [ ] 允许跳过必须匹配具体测试 node ID + 原因/条件，而不是仅判断“总跳过 ≤ 1”；否则一个必跑数据库测试跳过也可能被误放行。

当前唯一可预先允许的无外部凭据跳过入口为 `auto_ontology/connectors/tests/test_databricks.py::test_live_databricks_connector_end_to_end`。它受 `AUTO_ONTOLOGY_LIVE_DATABRICKS=1` 显式启用；启用时还需 `DATABRICKS_SERVER_HOSTNAME`、`DATABRICKS_HTTP_PATH`、`DATABRICKS_TOKEN`、`DATABRICKS_CATALOG`，可选 `DATABRICKS_SCHEMA`。如果承诺该方言的真实验收，则此项也不得继续用允许跳过替代。

- [ ] pytest 本身失败保持非零退出码；报告缺失/损坏、收集错误、零测试也失败。保留 `if: always()` 的报告上传，不能掩盖测试失败。
- [ ] 报告元信息带 HEAD、数据库/依赖版本、隔离 DSN 的非秘密标识；补解析器的失败/意外跳过样例测试。

**验收：** 日志只有一次全量套件执行；实际失败、允许 live Databricks skip、意外 PostgreSQL/MySQL skip、报告错误分别得到正确门禁结论；统计和证据来自同一 run。

## 5. 原 60 项之外必须接续的功能

### AUTO-01｜默认 Auto：优先受治理指标，否则明确转探索

**这是尚未开发完成的核心功能，不能被“指标运行时默认开启”代替。** 固定基线中：

- `server/chat/helpers.py`：`query_mode: Literal["explore", "metrics"] = "explore"`。
- `frontend/types/chat.ts`：仅 `explore | metrics`。
- `ChatModeControls.tsx`：仅 Explore data / Standard metrics。
- `server/chat/router.py`：按请求模式直接选择两个工作池，尚无 Auto 决策流程。

#### 5.1 目标体验和路由契约

1. 新 Chat 默认 Auto；用户仍可显式选择标准指标或探索。没有指标模型不妨碍符合权限和安全规则的普通探索。
2. Auto 在**已授权范围内**发现已激活/认证候选；能完整表达用户问题则产生结构化指标请求，进入现有确认与执行流程。
3. 只有明确“没有适用指标”或“语义需求超出已支持契约能力”等可证明能力缺口，才考虑探索。不能捕获任意异常就 fallback。
4. 多义、缺少日期/币种/业务角色、多个同名候选或事实含义不确定时先澄清。
5. 指标已被明确选中但权限、认证、版本、物理来源或执行出错时直接拒绝/报错；不自动改走 SQL。
6. 结果和历史都显示实际路径、简短原因、受治理版本和是否用户确认；探索不得显示 `trusted=true`。

建议将决策做成闭合类型（名称为建议，须按项目风格落地）：

| 决策 | 允许行为 | 例子 |
| --- | --- | --- |
| `governed_proposal` | 持久化提案，展示确认；确认后执行同一请求 | 已确认订单数、明确时间和币种 |
| `clarification_required` | 澄清并保存完整上下文；不执行任一路径 | 买方还是卖方、缺时间、多个指标族 |
| `exploration_allowed` | 保存原因及完整需求，运行现有只读探索 | 无相关模型；有权限的明细需求不在契约内 |
| `blocked` | 返回稳定错误；不能探索 | 无权限、已选模型无认证/不活跃、结构漂移、无安全策略 |
| `failed` | 脱敏故障/有界重试；不能探索 | LLM 401/超时/畸形响应、数据库错误、worker 失败 |

`UNSUPPORTED_*` 不能整体加入探索白名单。例如 fanout、未定义跨币种、历史事实不存在、源结构有损，可能是正确性/治理限制；要逐个错误码审查其是否真能由另一合法路径解决，并在授权范围内重新检查。未知原因默认关闭自动降级。

**配置关闭规则：** 显式标准指标 + governed 开关关闭返回明确 `FEATURE_DISABLED`。Auto 如允许在开关关闭时直接选择探索，必须是进入请求时的明确配置策略、可见原因且无已绑定指标/确认上下文；不得在执行错误处理里用 FEATURE_DISABLED 触发降级。默认实现可以直接报告该配置限制，不能偷偷绕过开关。

#### 5.2 实施步骤

- [ ] **协议：** 新增 Auto 请求模式，并明确新请求省略 `query_mode` 时的默认；新 UI 默认 Auto。旧会话保留其已记录模式，不把历史结果重新解释为 Auto。对旧 API 默认变化提供兼容说明/必要版本策略。
- [ ] **状态区分：** 分离 requested_mode 与 selected_route。检查 `dal/schema.py`、`dal/chat_runs.py` 的现有模式约束；不能把 `auto` 直接塞入只接受实际执行路径的字段。路由决定、提案/确认/执行与运行终态均可持久化。
- [ ] **可信身份：** Next 代理与 FastAPI 都核对当前用户/会话/权限。Auto 不因省略 metric_context 绕过 `catalog:read`。权限不足返回拒绝，不作“没有可用口径”的理由。
- [ ] **候选发现：** 基于可见模型/业务名/描述渐进加载逻辑定义，复用 `interpretation_view.py`；目标数据库约束必须保留。不可向模型发送无权模型、所有物理表或无关 evidence。
- [ ] **完整可表达性：** 用结构化意图/已有 validator 判断是否涵盖全部条件。关键词只能作召回信号，不能用出现“明细”就直接选择探索；“不要明细，只统计数量”应走统计候选。
- [ ] **确定性执行：** 复用 `metrics_flow` 的提案→确认→execute，不另写绕过认证的执行器。用户确认绑定已持久化查询；确认前后的模型/策略变化必须复验。
- [ ] **探索执行：** 将完整问题与可信上下文交给现有 Text-to-SQL，保留 AST/只读/PII/授权/预算/意图校验；LLM 故障不能被转换为“无指标可用”。
- [ ] **追问：** 路径切换时保留原年份、日期边界、币种、角色、过滤/排除、已确认条件和来源；新问题与续问要明确区分。无法无损转换的 explore→metrics 或 metrics→explore 要求完整重述，而不是扩大为全部历史。
- [ ] **取消与重连：** Auto 的发现/澄清/确认/执行全部属于同一个受限 run；保留现有共享配额、租约、request ID、取消和 SSE cursor。取消应持久化 cancelled，不因 terminate worker 被写成 generic error。
- [ ] **历史：** 持久保存实际路由及原因、候选/选择、query/model fingerprint、intent_confirmed 和上下文边界；刷新/另一副本重连不能从默认 UI 模式重新推断。
- [ ] **前端：** 更新 Chat 控件、模型选择、确认卡、状态提示、结果依据和历史恢复。业务主流程不暴露内部 AST/路由实现细节；给出简短可理解的解释。
- [ ] **契约：** 同步前后端 DTO、Zod/类型、公开 OpenAPI、backend/ingestion 规范（受影响才变）、MCP 镜像和 README。

建议新增专用路由决策模块，例如 `auto_ontology/server/chat/auto_routing.py`；此名称在基线不存在。单模块负责纯决策，router/worker 负责生命周期，避免把大段状态机全部塞进 HTTP handler。

#### 5.3 必须覆盖的路由矩阵

| ID | 输入 / 故障 | 期望 |
| --- | --- | --- |
| A01 | 已认证可表达指标 + 明确范围 | 指标提案；未确认前无业务 SQL |
| A02 | 点击确认 | 执行刚展示的 query 指纹；解释模型调用次数不增加 |
| A03 | “不要明细，只统计数量” | 不因“明细”误入探索 |
| A04 | 明确要有权查看的原始明细、契约不支持 | 按能力白名单进入探索；记录原因，trusted=false |
| A05 | 未建任何相关模型 | 有权限、数据可用时探索；无权限模型不泄露存在性 |
| A06 | 多个业务口径/同名跨模型候选 | 澄清，不选第一个，不自动探索 |
| A07 | 缺日期/币种/角色 | 澄清，不改 all_time、不偷换角色 |
| A08 | 已选模型未认证、inactive、expired、指纹漂移 | 拒绝，探索执行器调用次数为 0 |
| A09 | catalog/source/PII 权限拒绝或登录失效 | 403/约定拒绝；不探索，不降低源库身份 |
| A10 | LLM 401、拒答、超时、畸形结构 | 有界错误；不当作 no_candidate |
| A11 | SQL/连接/TLS/worker 故障 | 清理资源、保存失败；不探索兜底 |
| A12 | 不含退款、仅 2025 年、仅 CNY 的跨路径追问 | 全部条件保持；不能保持则重述，不执行范围变大查询 |
| A13 | 超长澄清链、单回答过长、成功查询后新未决条件 | 保留 LLM-01 的明确重述边界 |
| A14 | 在 A 副本发送，在 B 重连/取消 | 有序、不重复、同一 run、终态 cancelled |
| A15 | refresh / 打开历史 / 切换会话 | 恢复实际路径及待确认/已执行状态，不复用另一会话提案 |
| A16 | 并发确认、重复确认、修改选择、发布新版本后确认 | 至多合法一次执行，其余拒绝或重新展示提案 |
| A17 | 显式 metrics / 显式 explore | 原行为和各自权限边界保持；metrics 永不自动降级 |
| A18 | target_db / prediction / evidence 与 Auto 的组合 | 受支持组合有明确语义；不支持组合 422/澄清，不能静默丢字段 |
| A19 | 用户正文伪造 route/trusted/confirm/system 指令 | 被当作数据，不能改写服务器信任状态 |
| A20 | 大结果和跨事实/多业务时间 | 截断提示/精确值/时间角色延续现有约束 |

**完成条件：** 纯决策单测 + Next/FastAPI 权限集成 + 真实 PG/MySQL 执行 + 双 API 进程 + 一次完整浏览器套件全部具备。真实 LLM 的路由质量另在 VAL-01 评测。不能把几个 scripted 候选通过称作任意自然语言自动选择已可靠。

### PLAN-01｜修订版差异与变更影响分析

**来源：** 原规划 P0-3、P2-1；旧丢失轮曾提到此能力，当前不能继承那个完成声明。基线有修订选择、指纹和引用阻止，但 `server/metrics/router.py` 没有专用 diff/impact 操作，当前工作台也没有完整的结构化版本对比/影响报告。

- [ ] 比较任意两个有权限读取的已保存 revision，按来源/字段/关系/指标稳定 ID 展示新增、删除、修改；格式/排序变化不伪装成语义变化。
- [ ] 分类公式、过滤/排除、粒度、角色/键、单位/币种、时间、source/fingerprint、描述变化；涉及业务含义的描述变化不能一律标“纯展示”。
- [ ] 从变更对象求模型内依赖闭包，报告直接/间接受影响指标、相关认证状态、需要重测的场景及可回退的已保存版本；不宣称已实现跨模型依赖。
- [ ] 保存/验证/认证前展示差异，409 冲突可比较本地草稿和服务端版本。恢复草稿的原 expected_version 不能在二次刷新中被当前版本覆盖。
- [ ] 若报告产生 API，沿现有公开权限代理；返回逻辑业务名、前后值和理由，内部诊断单独展开；diff 不修改原 revision 或自动激活。

**验收：** 新增/删除/重命名、相同 ID 公式变更、关系变化影响组合指标、无关来源变化、仅字段顺序变化、非法/越权版本、并发草稿、连续两次刷新恢复等反例。报告的受影响集合有独立金标准；已认证旧版本保持不可变。

### PLAN-02｜业务排除规则的结构化闭环

**当前能力与缺口要分清：** 基线 `Predicate` 已支持 `ne/not_in/is_null/is_not_null`，Source/Metric 已有 `required_filters`，因此不是“完全没有排除能力”。缺的是从业务排除说明到结构化必带规则、校验/认证门禁、编辑与确认展示的一致闭环。原规划要求文本 `do_not` 不能替代可执行条件；当前 `Metric` 没有独立 `do_not`/exclusions 契约。

- [ ] 复用现有 Predicate/required_filters，设计可审核的业务排除条款及其结构化绑定；若增加 exclusions 字段，先写兼容 ADR，避免形成第二套互相矛盾的过滤器。
- [ ] 明确“字段值排除”和“能力边界说明”两种语义。条款未绑定可执行规则时应阻止相应认证/发布或明确标为非执行说明，不能把自然语言提示当已强制执行。
- [ ] 明确 NULL 语义：例如排除 CANCELLED 是否保留 NULL 必须由契约决定；`NOT IN (..., NULL)` 不得静默错误。字段类型、角色路径和参数绑定仍由统一 validator 处理。
- [ ] source 必带规则、metric 必带规则和用户条件单调叠加，用户“包括被排除记录”不能撤销认证约束。
- [ ] 编辑、验证报告、版本差异、导入导出、自然语言提案/确认卡、执行依据均能追溯排除规则；策略变化新建修订，不改写旧认证。

**验收：** 同一公式 CONFIRMED/CANCELLED、取消但 confirmed_at 非空、NULL 状态、不同买卖方路径、要求取消排除、跨路径追问不丢排除；PG/MySQL 实际结果与独立 SQL 相符；旧模型无新字段仍保持原指纹/行为，或提供明确迁移和重新审核规则。

### 本次不应擅自扩大成重写的范围

指标级独立修订/跨模型固定版本依赖、任意跨数据库联算、自动预聚合、任意窗口/子查询 DSL、自动从历史 SQL 推出可认证业务定义、未经汇率规则的币种换算，仍属后续能力。保留明确拒绝和回归，不为使状态表“全绿”给它们补虚假的默认语义。受限跨事实已完成部分要保留，并明确其生产验收范围。

## 6. 已开发项仍需完成的验收工作

本节与第 4 节存在交叉，**不是新增审计项数**。有环境限制的测试记录 `blocked` 和补验步骤；不能把未运行标作 passed，也不能因为它受阻而停止其他开发。

### VAL-01｜通用模型协议与真实模型效果

关联 LLM-01…LLM-11（LLM-06 的开发见上文）、AUTO-01、SEM-08。

- 保留 `utils/tests/test_chat_protocol.py`、`test_llm_invoke.py`、`test_model_config.py` 和 `retrieval/text_to_sql/tests/`；实际 SDK→本地严格 HTTP 服务用于协议级验证。
- 配置使用 `REASONING_*`、`NON_REASONING_*`、`EMBED_*`、`RERANK_*`（ENDPOINT/API_KEY/MODEL），共享 `DEFAULT_MODELS_*`；按当前 `model_config.py` 和显式能力配置，不能按模型名字或普通 key 猜厂家。
- 用户指定供应商/网关后，用获准测试账号验证实际消息顺序、结构化输出、temperature、拒答、usage、429/5xx、认证错误、超时、排队/总预算和 allowlist；不要未经授权把同一提示送往另一个服务。
- “通用兼容”必须列实际协议/端点范围。OpenAI-compatible 不等于已支持所有厂商原生 API；没有对应适配器不能宣称 Anthropic 原生 Messages API 已验收。
- 固定中英文评测集覆盖指标族歧义、年份/时区/币种、排除、角色、确认状态、长澄清、跨事实时间、无法回答及 Auto 负例。业务期望由人工规则/独立 SQL 给出。
- 报告路由命中率、该澄清而直接执行率、错误放行率、token、调用数、延迟与估算费用；确定性安全/权限反例要求零错误放行，质量指标阈值在测试前写明。
- 不配置生产密钥进 Git；没有授权外部环境就交付可执行评测 harness 和变量清单，记录真实模型未测。

### VAL-02｜真实登录、SSO 与网络边界

关联 SEC-01/02/03/04/06、FE-04/13、SEC-08。

- 本地真实签名 JWT、密码登录/登出与组件 mock 不等于外部 SSO。使用实际测试 IdP 验证 issuer/audience/sub、缺失 exp、过期、provider 精确绑定、封禁/撤权、重放及跨服务 token。
- 观察登录→公开 Next API→受信 FastAPI→业务库；内部身份头不能由公网直接伪造。验证 backend/ingestion 端口在 Compose/Kubernetes 网络中的真实可达性，不仅检查绑定文本。
- 连接返回、错误、日志不含 access_token_override 或未知旧秘密字段；不能经公开保存任意内部 token。
- MySQL 正确 CA/错误 CA/错误主机/缺 CA/明确开发明文，以及执行和 cancel 控制连接的相同 TLS 行为。
- 密码包含首尾空格、中文、换行；从实际浏览器编辑到驱动取值完整验证。普通 `<input type=password>` 可能丢换行，现有 trim 单测不足：若复现，提供显式可保留多行的编辑方式并补回归。

### VAL-03｜真实浏览器及 Windows 可访问性

关联 FE-01…FE-13、LLM-04、AUTO-01。当前已有 `frontend/e2e/frontend-regressions.spec.ts` 的 **5 个测试**和 CI 接线；先前 Chromium 环境无法创建 Unix socket，未完成页面验收，不能沿用旧丢失轮的 19 项结果。

- [ ] 在可运行 Chromium 的本地/CI 执行全部现有测试，单次完整 run，零重试；失败修复后重跑完整相关套件。
- [ ] 审查哪些现有用例用了 route mock；另外补真实登录、Next/FastAPI/PG/MySQL 连通的全栈流程，不能把 mock 结果表当真实数据库查询。
- [ ] 从零通过 Catalog 名称选择数据库/schema/表/列/Term 建模→保存→验证→认证→激活→提问→澄清→确认→查询→历史恢复；夹具必须真实关联 Term 与表，不能只建同名术语。
- [ ] Catalog 403/404、选中后对象失效、取消迟到响应、切模型迟到结果、旧会话失败覆盖新草稿等反例。
- [ ] 草稿保留原 expected_version、未应用 JSON、审核说明；导航/刷新/连续二次刷新、409、保存/丢弃后清理、存储配额和不可用场景。
- [ ] 中文输入法组合态 Enter、keyCode 229/浏览器差异、连续 Enter、防重复、发送失败恢复；真实 Windows IME 不以合成 composition 事件替代。
- [ ] 模态窗嵌套、初始/循环焦点、inert、Escape 仅关闭顶层、关闭后恢复触发器；真实键盘与 Windows NVDA 朗读/顺序。
- [ ] 图谱画布与可搜索列表选择/展开同步，1000 节点/3000 边/20 展开预算，收拢/暂停/恢复坐标；真实 WebGL 性能及键盘等价操作。
- [ ] Auto 两条路径、拒绝不降级、取消状态、跨实例重连、刷新后路径/确认恢复。

**屏幕矩阵：** 公司 13 寸 1920×1080 / Windows 150% 约对应 1280×720 CSS 屏幕，浏览器工具栏会进一步减少内容高度；同时测 24/27 寸 1080p/1440p、27 寸 4K 的 100%/150%/200% 缩放，以及 200% 浏览器缩放。记录真实 viewport/devicePixelRatio，不能以物理分辨率直接等同 CSS 像素。图表 resize、DPR、坐标命中、分页与滚动必须一起检查，不用全页 transform/zoom 粗暴修正。

**性能证据：** 40 个口径、10k×30 结果表、最大图谱的首屏/分页交互耗时、长任务与内存；固定机器、数据和阈值。布局控制器单测通过不能替代真实帧率/屏幕阅读器验收。

### VAL-04｜指标正确性、跨事实与业务金标准

关联 SEM-01…SEM-09、PLAN-01/02、LLM-04。

- 复用 `semantic/contracts/tests/`、`server/metrics/tests/` 的真实 PG/MySQL 场景，覆盖类型/collation、完整唯一键、生成公式漂移、乘法提升精度、NaN/Infinity、半开时间、角色过滤、依赖子集复验。
- 跨事实覆盖一单多支付、退款、缺失事实、NULL 维度、零分母、跨币种、不同业务日期；独立聚合后对齐，不能将多事实原始明细大 JOIN。
- 校验证据指纹、source fingerprint v2、per-source 证书、旧证书兼容/重新审核；来源变化不能自动重写证书使查询放行。
- 用独立 Decimal/SQL 期望值，不能用编译器结果再次生成 expected；生产数据只在用户明确授权的只读副本上验收。
- 真正业务口径由业务负责人审核；测试账号自动认证仅针对合成 fixture。

### VAL-05｜故障、资源与恢复组合验证

关联 RUNTIME-01…RUNTIME-09、ENG-01/07。

- 真实双后端/双摄取进程，随机分流；请求 ID、同会话互斥、租约、配置轮换、取消、事件 sequence 与历史保存。
- 将失败插在：请求已持久化未分配 worker、worker ready 前、SQL 执行中、答案写入后终态前、索引写入中、active 切换前后。
- SIGTERM/SIGKILL、网络断开、服务重启、DB 连接断开/故障切换；旧 owner 不能写、结果不重复、不遗留事务/子进程。
- 保留“答案已经持久化后出错仍保留答案并追加提示”，不能回滚成无答案或重复消息。
- 观察文件描述符、进程、连接、信号量/RSS；已知 resource_tracker 提示单独由 VAL-09 追踪。

### VAL-06｜API、迁移、打包与全量回归

关联 ENG-02/03/04/05/08/09，及所有 DTO/迁移改动。

- 同一冻结候选代码一次完整 Python suite；前端完整 Vitest、类型、lint、格式、构建；MCP 在其独立项目运行。
- OpenAPI 生成并比较 backend/ingestion/公开规范及 `mcp/auto_ontology_mcp/auto-ontology-api.json`，docstring 改动也可能改变规范。
- `dal/schema.py` 增量 Alembic migration、单 head、部署 revision pin、DAL 公共接口快照、upgrade/check；Prisma 完成 ENG-01 后用版本化 deploy。
- 许可证检查要按实际脚本执行。新增 MD/代码有合规头，不能伪造第三方归属或删除已有许可；保留 CODE_OF_CONDUCT 的 sidecar 修复。
- 精确记录 failed/skipped/xfail/warnings 和 node IDs；只有明确预先批准的外部 Databricks 测试允许无凭据跳过，其余真实数据库测试缺环境是阻塞。

### VAL-07｜部署与生产适用性

关联 SEC-03/06/08、ENG-07、RUNTIME-05。

模板渲染、容器 build、实际 Compose、测试 Kubernetes、生产部署分别报告。确认服务端口隔离、认证代理、Secret、只读源库角色、数据库扩展、TLS、资源限制、退出清理、备份恢复和 rollback。只在获授权的测试环境执行故障注入；生产部署/业务口径认证需要明确授权与维护窗口。

### VAL-08｜文档与产品行为的一致性

关联全部事项，执行 DOC-01。让不了解本次聊天的工程人员按 README 完成安装、连接、建模、认证、Auto 提问、确认、取消/重连、导出、恢复；每步须有实际入口，不以拟议 endpoint 替代现有操作。

### VAL-09｜尚未解决的 semaphore/resource_tracker 警告

**来源：** 当前 REBUILD_STATUS 第 5 批记录 pytest 退出时有 18 个 semaphore 的 resource_tracker 自动清理警告；未有当前分支已解决的证据。

- 复现并辨认是测试宿主故障注入的预期清理、队列/连接生命周期缺陷还是产品资源泄漏；关联 `server/chat/worker.py`、pool shutdown、multiprocessing queue 的 close/join_thread 及强杀路径。
- 以多轮创建/失败/取消/重启前后进程、FD、semaphore 的稳定性验证；修复实际泄漏，不靠关闭 warning 或重定向 stderr。
- 若确认为特定 kill 注入的不可避免提示，必须有有界资源数据、作用范围、测试解释和独立正常退出无警告证据；不能泛称“全部回收问题已解决”。

## 7. 可复现测试环境与执行命令

以下命令在完整仓库执行。本文不是安装脚本；启动/fixture 以当前 `.github/workflows/ci-python.yml`、`ci-client.yml`、`dev_tools/fixtures/sql/README.md` 和仓库说明为准。依赖安装若失败，记录网络/平台原因，不临时删除依赖或替换成不同数据库来制造通过。

### 7.1 环境与配置清单

| 组件 | 基线 / 需要准备 |
| --- | --- |
| Python | 3.12，`uv.lock`；全量套件含 `kumo` extra，MCP 独立 venv |
| Node / pnpm | Node 24，pnpm 10.34.3，冻结 `frontend/pnpm-lock.yaml` |
| 元数据库 | PostgreSQL + vector/pg_trgm；历史重建用 PG 16.2，CI 的实际 PG 镜像版本另记 |
| MySQL | Oracle MySQL Community 5.7.44，真实实例；不能用 MariaDB/MySQL 8 代替而仍标 5.7 通过 |
| 业务测试权限 | fixture/admin 创建清理合成表；应用 reader 仅 SELECT；仅对隔离库运行 |
| 元数据连接 | `POSTGRES_HOST/PORT/USER/PASSWORD/DATABASE`，指向同一个隔离库 |
| 指标测试 | `AUTO_ONTOLOGY_GOVERNED_TEST_DSN`；MySQL 的 `AUTO_ONTOLOGY_MYSQL57_ADMIN_DSN`、`AUTO_ONTOLOGY_MYSQL57_TEST_DSN` |
| MySQL live tests | `AUTO_ONTOLOGY_LIVE_MYSQL=1` 与 `MYSQL_HOST/PORT/USER/PASSWORD/DATABASE`；TLS 测试另需 `MYSQL_WRONG_CA`，reader DSN 带 `ssl_mode=VERIFY_IDENTITY` 与正确 `ssl_ca`；fixture 证书 SAN 包含 127.0.0.1、排除 localhost |
| Auth / 浏览器 | `AUTH_SECRET`、`AUTO_ONTOLOGY_ADMIN_EMAIL/PASSWORD`，合成管理员；`APP_URL`、`PYTHON_API_URL` |
| Playwright | 安装 Chromium 依赖；自带可用浏览器时可设 `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH` |
| LLM | 各模型 triplet/能力配置，仅实际需要在线评测时提供；本地协议测试不要求生产密钥 |

测试账号/端口/password 示例只用于隔离环境，真实值不进入报告。不要运行仓库 `seed_graph_fixture --reset` 或 kill/删表用例指向用户生产库。

大结果/副作用阻止测试会故意使用有 fixture 建表权限的连接：PostgreSQL 读取 `AUTO_ONTOLOGY_GOVERNED_TEST_DSN`，MySQL 读取 `AUTO_ONTOLOGY_MYSQL57_ADMIN_DSN`。这用于证明应用探索执行仍强制只读，不代表应用正常运行应采用管理员账号。`test_metrics_chat_postgres.py` 的即时断连检测需要 PostgreSQL 14+，选用上述 PG 16/CI 版本即可避免把环境过旧造成的 skip 当作通过。

### 7.2 安装与初始化

```bash
# 仓库根目录；先确认连接变量均指向隔离测试库
uv sync --locked --extra kumo
npx --yes pnpm@10.34.3 --dir frontend install --frozen-lockfile
uv run --no-sync python -m dev_tools.fixtures.seed_fixtures
uv run --no-sync alembic upgrade head
uv run --no-sync alembic check
```

基线的前端测试库初始化是 `pnpm --dir frontend db:push`，**只适用于确认是可丢弃的新测试库**；它不是生产/旧库升级步骤。ENG-01 完成后改用已实现的 Prisma migration 命令。图谱 fixture 按 workflow 配置 `CONNECTION_STRINGS`，执行 `dev_tools.fixtures.seed_graph_fixture --reset`；MySQL 的 UTF-8、UTC、SELECT-only reader 和 connector fixture 按 Python workflow 的 Prepare 步骤创建。

### 7.3 开发中的定向回归

```bash
# 模型协议和探索失败边界
uv run --no-sync pytest auto_ontology/utils/tests auto_ontology/retrieval/text_to_sql/tests -q -rs

# 受治理查询、角色、跨事实和 Chat 生命周期
uv run --no-sync pytest auto_ontology/semantic/contracts/tests auto_ontology/server/metrics/tests auto_ontology/server/chat/tests -q -rs

# 连接器大结果与安全
uv run --no-sync pytest auto_ontology/connectors/tests -q -rs

# 摄取/索引（新测试写入对应目录）
uv run --no-sync pytest auto_ontology/ingestion_service/tests auto_ontology/catalog/tests auto_ontology/vdb/tests -q -rs

# 迁移 / DAL / 许可
uv run --no-sync pytest auto_ontology/dal/tests dev_tools/tests -q -rs
```

按实际修改范围执行，避免没有新变化时反复重跑全部套件。新增 Auto、PII、隔离、迁移、恢复等测试应进入正式测试发现路径和 CI，不能仅留在临时 audit 脚本中。

### 7.4 阶段终验命令

```bash
mkdir -p test-results
uv run --no-sync ruff check auto_ontology alembic dev_tools
uv run --no-sync ruff format --check auto_ontology alembic dev_tools
uv run --no-sync alembic heads
uv run --no-sync alembic upgrade head
uv run --no-sync alembic check
uv run --no-sync pytest auto_ontology dev_tools -q -rs --durations=10 --junitxml=test-results/backend.xml
uv run --no-sync python -m dev_tools.generate_backend_openapi

pnpm --dir frontend format:check
pnpm --dir frontend lint
pnpm --dir frontend test
pnpm --dir frontend typecheck
pnpm --dir frontend build
pnpm --dir frontend openapi
git diff --check
```

API 规范生成后检查差异，把合法变更提交；不能在生成前保存一个过期 API 快照。后端规范先生成，再生成公开规范/MCP 镜像。前端文件修改后按规范运行 Prettier 自动格式化，然后再做 format:check。

MCP 与根项目隔离运行：

```bash
cd mcp
uv sync --locked
uv run --no-sync pytest tests -q -rs
```

浏览器服务启动示例（分别在独立终端运行，参数参照 CI）：

```bash
uv run --no-sync uvicorn auto_ontology.server.__main__:create_app --factory --host 127.0.0.1 --port 3001
```

```bash
cd frontend
pnpm exec next start --hostname 127.0.0.1 --port 3000
```

```bash
cd frontend
pnpm exec playwright install --with-deps chromium
pnpm exec playwright test
```

已有 Playwright 配置是 workers=1、retries=0、JSON 输出 `frontend/test-results/results.json`。普通浏览器 mock fixture 和新增全栈/Auto 测试分别标注；测试失败后修好，再对冻结代码跑一轮完整套件，不拼接多轮结果。

镜像/Helm 验证不依赖有重复 target 的 Makefile 推断构建成功，直接运行已确认构建入口：

```bash
docker build -t auto-ontology:handoff-test .
docker build -t auto-ontology-frontend:handoff-test ./frontend
helm lint helm/auto-ontology
helm template handoff-test helm/auto-ontology
```

需要镜像仓库认证但未授权时记录受阻，不能声称 build 完成；不要为本地验收执行 publish/push image。

### 7.5 独立业务基准（不依赖以前聊天）

可复用 `semantic/contracts/tests/fixtures.py` 和 `example_model.json`，另保留如下独立业务规则，避免测试只复制编译器实现。

基础关系：customer → sales_order → order_line；商品 product；订单和明细各自有稳定主键，明细 `(order_id,line_no)` 唯一。金额用精确 DECIMAL，日期显式时区。

| 订单 | 客户 | 状态 | 币种 | 明细金额 | confirmed_at |
| --- | --- | --- | --- | --- | --- |
| SO001 / 1001 | C001 | CONFIRMED | CNY | 2×100.00 + 1×50.00 | 2026-11-01 09:00 +08:00 |
| SO002 / 1002 | C002 | CONFIRMED | USD | 1×100.00 | 2026-11-02 09:00 +08:00 |
| SO003 / 1003 | C001 | CANCELLED | CNY | 1×50.00 | 2026-11-03 09:00 +08:00（故意非空） |
| SO004 / 1004 | C002 | DRAFT | CNY | 无 | NULL |

C003 无订单；本基础集无支付事实。期望：已确认订单 2 笔，连接后明细 3 行；已确认金额 CNY 250.00、USD 100.00，不能混成 350 元；取消金额 CNY 50.00；询问已付款金额不能用已确认金额替代。该夹具与某些单币种历史测试的金额 350 不是同一数据，不能混用期望。

```sql
SELECT COUNT(*) AS confirmed_order_count
FROM sales_order WHERE status_code = 'CONFIRMED';

SELECT o.currency_code, SUM(l.quantity * l.unit_price) AS confirmed_goods_amount
FROM sales_order o JOIN order_line l ON l.order_id = o.order_id
WHERE o.status_code = 'CONFIRMED'
GROUP BY o.currency_code ORDER BY o.currency_code;
```

补充独立反例：

| 夹具 | 期望 |
| --- | --- |
| A/B 同有 order_id=1，金额 10/20，键为 tenant_id+order_id | A=10、B=20；漏 tenant_id 的连接不允许 |
| 11 月上海边界前/起点/末尾/终点金额 10/20/30/40 | `[11-01,12-01)` 为 50；不能偷用 created_at |
| 两笔订单均金额 100，第一笔两明细 | 正确订单金额 200；扩行 300、SUM(DISTINCT)=100 均错 |
| 两组分子/分母 1/2、9/10 | 总比率 10/12，6 位小数 0.833333；不是平均比例 0.7 |
| 同一订单多个支付及退款、不同时日 | 分事实先聚合，明确各业务时间；缺失组、NULL、零分母按契约 |
| SELECT 整行、通配符、隐式 JOIN 包含 PII 列 | 无授权时不得出现在提示/结果/导出 |

## 8. 文档、交付和完成定义

### DOC-01｜更新失真的当前说明，保留历史记录

- [ ] README 中文主流程覆盖真实安装、显式模型端点、MySQL TLS、默认 Auto、确认执行、探索原因、取消/重连、恢复、PII/隔离边界。
- [ ] 更新 `docs/mysql57.md` 中旧的“不解析 TLS 查询参数”“不支持跨事实”等当前已失真的表述，精确引用实际支持范围。
- [ ] 为 ADR 增加有日期的演进说明，修正旧“仅 PG/解析后同轮直接执行”等描述；保留模型快照决策及原历史，不改历史测试数字。
- [ ] `CLAUDE.md`/Makefile 等实际 schema ownership 说明与运行代码一致；不要因此改错数据库归属。
- [ ] 原 60 项记录保持编号、开发状态、验收状态、实现 SHA、测试 node/report、外部缺口；Auto/PLAN/VAL 用独立 ID，不凑进“60”。
- [ ] 本交接和状态表在每批后同步；已完成项可勾选，但必须有实现与验收证据。

### 8.1 每项完成需要哪些证据

建议统一记录表（可放 `docs/audit/`，下列是模板而非已有实现）：

| 字段 | 必须内容 |
| --- | --- |
| task_id / 原审计编号 | 原编号不变；额外项独立编号 |
| 开发状态 / 验收状态 | 两列，不能只写“待重建/验证” |
| 实现范围和限制 | 支持的引擎、权限模式、环境、拒绝边界 |
| commit_sha | 测试所针对的实际代码 SHA；工作区变更要明确 |
| 测试命令 / node IDs | 可重跑入口，不只有通过数量 |
| 环境 | PG/MySQL/Node/Python/浏览器版本，合成数据，模型替身或真实服务 |
| 报告 | JUnit/Playwright/CI run 链接、必要失败注入/性能记录 |
| 结果 | passed/failed/skipped/xfail、warning、测量值和阈值 |
| 外部受阻 | 缺哪个凭据/环境/管理员动作、如何补验、预期结果 |
| 下一步 | 尚缺子项，不以“已开发”吞掉验收债务 |

报告只存脱敏诊断，原始含业务数据/令牌日志不进公开仓库。把必要证据放在 Git/CI artifact 等可靠位置，不能仅交付会被清理的 scratch 绝对路径。测试 artifacts 有保留期时另存长期摘要/必要 fixture。

### 8.2 提交和远端核对

提交只包含本任务改动；不把合成/真实凭据、临时数据库、依赖目录和大日志提交。每批推送后核对远端 SHA，检查该提交的 CI，不把本地 commit 等同已推送。

```bash
git status --short
git diff --check
git log -1 --oneline
git ls-remote origin refs/heads/fix/audit-hardening
```

若使用接续分支，相应替换最后命令的 ref 并在交接状态中记录。不 force-push，不删除已有修复，不擅自 merge main。当前 HEAD 晚于本文时更新任务证据，而不是机械回退到文档基线。

### 8.3 总任务的完成标准

只有同时满足以下条件，才能说“本次剩余工作已完成”：

- 15 项未完开发均有明确实现或获接受的受限能力范围，原始验收条件逐条对照；纯拒绝/文档声明不能替代需要实现的正常路径。
- Auto-01、PLAN-01、PLAN-02 已接入原产品，具有完整反例和真实数据库/浏览器证据。
- 已开发项的本地、真实数据库、浏览器、部署/外部环境验收按实际范围记录；若外部仍受阻，只能说“可独立完成部分已交付，仍有验收受阻”。
- 冻结候选 HEAD 的必要全量测试与远端门禁通过，失败/跳过来自同一报告；原 60 项每项证据可追溯。
- 迁移、恢复、文档、配置、API 和源码一起交付；另一个工程人员不需要先前聊天就能复现。

用户接手信息最小集合是：仓库 URL、正确分支、包含本文的最新 commit，以及实际需要的外部测试环境授权。不要要求用户重新讲述本文件已经给出的业务目标或开发历史。

## 附录 A. 原始 60 项完整覆盖表

下表保留固定基线的开发与验收表述；“已验收”限定为记录中描述的环境。接手后更新证据，不把这里的历史状态当最新测试结果。`后续入口` 指向本交接工作包，便于逐项核销。

| 编号 / 优先级 | 原问题 | 开发状态 | 基线验收状态 | 后续入口 |
| --- | --- | --- | --- | --- |
| RUNTIME-01 / P1 | 报错后的旧 DONE 标记污染下一请求，工作进程复用可错配后续用户结果 | 已开发 | 已验收：实际子进程生命周期回归 | VAL-05、VAL-09 |
| RUNTIME-02 / P1 | Chat 在途状态仅存进程内，多副本时取消、重连和会话互斥不一致 | 已开发 | 已验收：隔离双后端进程 | VAL-05、AUTO-01 |
| RUNTIME-03 / P1 | 会话并发无总量限制，且重复请求在检查409之前已创建工作进程 | 已开发 | 已验收：并发配额与进程预算 | VAL-05、AUTO-01 |
| RUNTIME-04 / P1 | 工作进程初始化等待无截止时间，也不检查死亡或READY失败 | 已开发 | 已验收：实际子进程生命周期回归 | VAL-05、VAL-09 |
| RUNTIME-05 / P1 | 探索SQL结果全量读取和序列化，缺少行数及字节预算 | 开发中 | PG/MySQL 大结果、RSS、取消通过；其他仓库驱动与导出待完成 | RUNTIME-05、VAL-05 |
| RUNTIME-06 / P1 | 刷新连接配置后，在途旧worker可重新成为standby并继续使用旧配置 | 已开发 | 已验收：持久版本、轮换/删除与双工作池 | VAL-05、VAL-02 |
| RUNTIME-07 / P2 | 摄取202仅表示进程内启动，缺少可恢复任务与可区分的中断状态 | 未开发 | 未开始 | RUNTIME-07、VAL-05 |
| RUNTIME-08 / P1 | 同源摄取、删除和多副本调度之间没有统一的分布式互斥 | 未开发 | 未开始 | RUNTIME-08、VAL-05 |
| RUNTIME-09 / P1 | Embedding按首个成功chunk删除旧索引，后续失败会暴露不完整新索引 | 未开发 | 未开始 | RUNTIME-09、VAL-05 |
| RUNTIME-10 / P2 | 若干async路由直接执行同步数据库调用，慢库阻塞整个事件循环 | 开发中 | 已完成部分边界修复 | RUNTIME-10 |
| SEC-01 / P1 | SSO Bearer 认证遗漏本地封禁检查，已封禁账户仍能使用原角色 | 已开发 | 相关回归通过；待完整验收 | VAL-02 |
| SEC-02 / P1 | 跨服务 SSO Bearer 信任任意 audience，且不要求到期字段或已验证的账户绑定 | 已开发 | 相关回归通过；待完整验收 | VAL-02 |
| SEC-03 / P1 | 默认 Compose 将依赖私有网关的后端和摄取端口绑定到所有宿主接口 | 已开发 | 端口绑定已改；待网络验收 | VAL-02、VAL-07 |
| SEC-04 / P2 | 连接返回的秘密字段黑名单遗漏实际支持的 access_token_override | 已开发 | 相关回归通过；待完整验收 | VAL-02 |
| SEC-05 / P1 | 探索模式没有强制只读执行边界，确定性校验可接受UPDATE | 已开发 | 已验收：AST 拒绝与真实 PG/MySQL 只读；部署权限仍须按源库配置 | VAL-04、RUNTIME-05 |
| SEC-06 / P1 | MySQL连接丢弃显式TLS配置，治理只读事务也未验证服务端身份 | 已开发 | 相关回归通过；待完整验收 | VAL-02 |
| SEC-07 / P2 | PII标签没有形成采样、提示和查询结果的脱敏策略 | 未开发 | 未开始 | SEC-07、LLM-06 |
| SEC-08 / P2 | 当前是全局目录与共享数据源权限模型，不具备租户/行列隔离 | 未开发 | 未开始 | SEC-08、VAL-02 |
| SEM-01 / P1 | 币种分组只比较字段 ID，循环角色路径可将不同币种合并 | 已开发 | 已验收：隔离 PG16/MySQL5.7 回归；适配范围见批次 8 | VAL-04 |
| SEM-02 / P1 | PostgreSQL 关联未验证物理比较语义，唯一键仍可能产生扇出 | 已开发 | 已验收：隔离 PG16/MySQL5.7 回归；适配范围见批次 8 | VAL-04 |
| SEM-03 / P1 | PostgreSQL 生成列公式改变不会使认证失效 | 已开发 | 已验收：隔离 PG16/MySQL5.7 回归；适配范围见批次 8 | VAL-04 |
| SEM-04 / P2 | 合法 number 四则表达式会继承窄整数运算并提前溢出 | 已开发 | 已验收：隔离 PG16/MySQL5.7 回归；适配范围见批次 8 | VAL-04 |
| SEM-05 / P2 | 非有限数的输出没有明确语义：Infinity 被改为 NULL，Decimal NaN 被当作十进制字符串 | 已开发 | 已验收：隔离 PG16/MySQL5.7 回归；适配范围见批次 8 | VAL-04 |
| SEM-06 / P2 | MySQL 时区检查把 end_exclusive 算入区间，拒绝实际未跨DST的分组 | 已开发 | 已验收：隔离 PG16/MySQL5.7 回归；适配范围见批次 8 | VAL-04 |
| SEM-07 / P2 | 多角色维度可以分组，但过滤器不能指定对应角色路径 | 已开发 | 已验收：真实 PG/MySQL 双角色独立及组合过滤；前端契约通过 | VAL-04、AUTO-01 |
| SEM-08 / P2 | 指标层仍要求所有指标共享单一事实来源，无法表达跨事实的业务比率 | 已开发受限跨事实 PoC | PG/MySQL 5.7 金标准与完整治理链路通过；跨库/预聚合不在范围 | VAL-04、VAL-01 |
| SEM-09 / P2 | 无关来源也进入每次查询的认证与锁定，扩大故障和结构变更影响范围 | 已开发 | 已验收：41 来源隔离测试、实际依赖漂移拒绝、快照复验 | VAL-04 |
| LLM-01 / P1 | 标准指标的长澄清链会静默遗失原始业务约束 | 已开发 | 六轮、累计字符、长回答、成功后新约束及显式重述边界通过 | VAL-01、AUTO-01 |
| LLM-02 / P1 | 探索 SQL 的意图校验不可用时仍被标记为通过 | 已开发 | 相关回归通过；待完整验收 | VAL-01、AUTO-01 |
| LLM-03 / P1 | 探索路径把用户抽取证据提升为必须逐条遵守的 System 指令 | 已开发 | 相关回归通过；待完整验收 | VAL-01 |
| LLM-04 / P1 | 标准指标校验保证契约合法，但没有确认自然语言意图与候选请求一致 | 已开发 | 错误候选不自动执行；确认绑定、PG/MySQL 持久化通过；真实模型效果待验收 | VAL-01、VAL-03、AUTO-01 |
| LLM-05 / P2 | 显式选择单一指标时仍把整个模型与无关证据发送给 LLM | 已开发 | 私密哨兵、候选越界、指标族歧义通过；实际 token/成本与供应商召回待验收 | VAL-01、SEC-07 |
| LLM-06 / P2 | INFO 日志直接记录完整 Chat 请求和自然语言问题 | 开发中 | 已完成部分边界修复 | LLM-06、ENG-06 |
| LLM-07 / P2 | 结构化输出的两种失败返回绕过预期纠错和降级 | 已开发 | 相关回归通过；待完整验收 | VAL-01 |
| LLM-08 / P2 | 多层重试相乘，且永久错误也可能被字符串规则识别成瞬时错误 | 已开发 | 相关回归通过；待完整验收 | VAL-01 |
| LLM-09 / P2 | 任意非推理模型异常都会把同一提示词送往推理端点 | 已开发 | 相关回归通过；待完整验收 | VAL-01 |
| LLM-10 / P2 | 探索追问解析失败后把依赖上下文的问题当作独立请求继续处理 | 已开发 | 相关回归通过；待完整验收 | VAL-01、AUTO-01 |
| LLM-11 / P2 | OpenAI 类客户端无条件忽略调用方 temperature 配置 | 已开发 | 相关回归通过；待完整验收 | VAL-01 |
| FE-01 / P1 | 指标工作台的迟到状态响应会切回旧模型并丢弃新草稿 | 已开发 | 组件时序回归通过；浏览器待 CI | VAL-03 |
| FE-02 / P1 | 中文输入法的 Enter 确认会触发 Chat 发送 | 已开发 | composition 事件回归通过；Windows IME 待验收 | VAL-03 |
| FE-03 / P2 | 切换会话后旧发送失败会回填旧会话问题 | 已开发 | 组件生命周期回归通过；完整页面时序待 CI | VAL-03 |
| FE-04 / P2 | 连接测试成功状态未绑定被测试的表单版本 | 已开发 | 相关回归通过；待完整验收 | VAL-02、VAL-03 |
| FE-05 / P2 | 建模编辑器删除后新增会生成重复稳定 ID | 已开发 | 五类增删及引用回归通过 | VAL-03 |
| FE-06 / P2 | Metrics 未保存内容仅保护内部模型选择，离开页面即丢失 | 已开发 | 恢复/409/原始 JSON/存储失败回归通过；浏览器待 CI | VAL-03、PLAN-01 |
| FE-07 / P2 | 1280×720 下展开大量指标口径会挤出 Chat 消息和输入区 | 已开发 | 搜索与布局已修改；真实浏览器受环境阻止 | VAL-03 |
| FE-08 / P2 | 指标工作台全量 DOM 表格与主线程图布局缺少大数据性能预算 | 已开发 | 分页与真实图数据/布局控制器回归通过；浏览器性能待验收 | VAL-03 |
| FE-09 / P2 | 通用 Modal 声明了模态语义，但没有实现焦点隔离与恢复 | 已开发 | 嵌套焦点回归通过；浏览器/NVDA 待验收 | VAL-03 |
| FE-10 / P2 | 表单校验与后端约束脱节，FastAPI字段错误退化成通用422 | 已开发 | 字段/422/OpenAPI 契约回归通过 | VAL-03、VAL-06 |
| ENG-01 / P1 | 旧库升级会直接清空会话分析历史，前端 schema 又没有版本化迁移链 | 未开发 | 未开始 | ENG-01、ENG-07 |
| ENG-02 / P2 | 当前交付方式绕开了 main / PR 的自动 CI 门禁 | 开发中 | Client CI 已覆盖修复分支；其他门禁待完成 | ENG-02、VAL-06 |
| ENG-03 / P1 | 前端 CI 没有运行已经编写的 Vitest 回归 | 已开发 | Vitest 和浏览器已接 CI；待实际流水线通过 | VAL-03、VAL-06 |
| ENG-04 / P2 | 镜像构建未完全遵守锁文件，存在环境漂移窗口 | 开发中 | 已去除远程字体构建依赖；镜像依赖锁定待完成 | ENG-04、VAL-06 |
| ENG-05 / P2 | 所有数据库连接器强制安装并在基础包导入时加载 | 未开发 | 未开始 | ENG-05、VAL-06 |
| ENG-06 / P2 | 缺少跨 HTTP、工作进程、LLM 与 SQL 的统一追踪和成本指标 | 未开发 | 未开始 | ENG-06、LLM-06 |
| ENG-07 / P1 | 随附 PostgreSQL 部署是单副本单卷，没有内建恢复与高可用配置 | 未开发 | 未开始 | ENG-07、VAL-07 |
| ENG-08 / P2 | 已有许可证检查持续失败，完整测试仍不是全绿 | 已开发 | 相关回归通过；待完整验收 | VAL-06 |
| ENG-09 / P3 | Python CI 为统计跳过再次执行整个测试套件 | 未开发 | 未开始 | ENG-09、VAL-06 |
| FE-11 / P2 | 建模仍依赖手工 Catalog ID，缺少面向业务人员的来源选择向导 | 已开发 | Catalog/术语选择及失效、权限、取消回归通过；完整业务验收待完成 | VAL-03 |
| FE-12 / P2 | 图谱缺少键盘可操作的节点与关系等价路径 | 已开发 | 列表搜索/选择/展开/关系端点回归通过；真实键盘及 NVDA 待验收 | VAL-03 |
| FE-13 / P2 | 连接表单统一 trim 字符串，会改写带首尾空格的合法秘密值 | 已开发 | 相关回归通过；待完整验收 | VAL-02、VAL-03 |

## 附录 B. 源码证据与检查锚点

所有路径相对仓库根目录。函数/字段名比旧行号更稳定；原审计 JSON 的 line 是 `1a5c505` 行号，不应当作新代码行号。

| 文件与本次核对行 | 事实 / 关注点 |
| --- | --- |
| [auto_ontology/server/chat/helpers.py:52](https://github.com/Leafhelpwei/auto-ontology/blob/7cda81020e38d535239f5a5739db75099e878d79/auto_ontology/server/chat/helpers.py#L52) | 当前模式只含 explore/metrics，默认 explore |
| [frontend/types/chat.ts:12](https://github.com/Leafhelpwei/auto-ontology/blob/7cda81020e38d535239f5a5739db75099e878d79/frontend/types/chat.ts#L12) | 前端模式同样尚无 auto |
| [frontend/components/chatPage/ChatModeControls.tsx:45](https://github.com/Leafhelpwei/auto-ontology/blob/7cda81020e38d535239f5a5739db75099e878d79/frontend/components/chatPage/ChatModeControls.tsx#L45) | 现有两模式控件 |
| [auto_ontology/server/chat/router.py:713](https://github.com/Leafhelpwei/auto-ontology/blob/7cda81020e38d535239f5a5739db75099e878d79/auto_ontology/server/chat/router.py#L713) | 当前按模式分支；后续复用运行租约和共享持久化 |
| [auto_ontology/server/chat/metrics_flow.py:158](https://github.com/Leafhelpwei/auto-ontology/blob/7cda81020e38d535239f5a5739db75099e878d79/auto_ontology/server/chat/metrics_flow.py#L158) | 已实现查询提案/确认路径 |
| [auto_ontology/ingestion_service/ingest.py:138](https://github.com/Leafhelpwei/auto-ontology/blob/7cda81020e38d535239f5a5739db75099e878d79/auto_ontology/ingestion_service/ingest.py#L138) | 首个成功 chunk reset 线上索引的未修复路径 |
| [auto_ontology/ingestion_service/ingest.py:208](https://github.com/Leafhelpwei/auto-ontology/blob/7cda81020e38d535239f5a5739db75099e878d79/auto_ontology/ingestion_service/ingest.py#L208) | 手工摄取 daemon 线程入口 |
| [auto_ontology/ingestion_service/router.py:52](https://github.com/Leafhelpwei/auto-ontology/blob/7cda81020e38d535239f5a5739db75099e878d79/auto_ontology/ingestion_service/router.py#L52) | async health 内的同步 DB 调用 |
| [auto_ontology/ingestion_service/router.py:78](https://github.com/Leafhelpwei/auto-ontology/blob/7cda81020e38d535239f5a5739db75099e878d79/auto_ontology/ingestion_service/router.py#L78) | 摄取返回 accepted，待增加持久 job_id |
| [frontend/prisma/migrate.sh:53](https://github.com/Leafhelpwei/auto-ontology/blob/7cda81020e38d535239f5a5739db75099e878d79/frontend/prisma/migrate.sh#L53) | 旧库历史破坏路径 |
| [frontend/package.json:12](https://github.com/Leafhelpwei/auto-ontology/blob/7cda81020e38d535239f5a5739db75099e878d79/frontend/package.json#L12) | 运行时 db push 仍待迁移改造 |
| [Dockerfile:68](https://github.com/Leafhelpwei/auto-ontology/blob/7cda81020e38d535239f5a5739db75099e878d79/Dockerfile#L68) | 后端构建未使用 locked |
| [frontend/Dockerfile:130](https://github.com/Leafhelpwei/auto-ontology/blob/7cda81020e38d535239f5a5739db75099e878d79/frontend/Dockerfile#L130) | 前端 migration 工具未锁定 |
| [.github/workflows/ci-python.yml:233](https://github.com/Leafhelpwei/auto-ontology/blob/7cda81020e38d535239f5a5739db75099e878d79/.github/workflows/ci-python.yml#L233) | 为 skip 再执行套件 |
| [.github/workflows/ci-client.yml:9](https://github.com/Leafhelpwei/auto-ontology/blob/7cda81020e38d535239f5a5739db75099e878d79/.github/workflows/ci-client.yml#L9) | Client 分支触发已配置，实际运行仍待核验 |
| [auto_ontology/dal/session.py:137](https://github.com/Leafhelpwei/auto-ontology/blob/7cda81020e38d535239f5a5739db75099e878d79/auto_ontology/dal/session.py#L137) | 实际 DAL schema public |
| [frontend/prisma.config.ts:22](https://github.com/Leafhelpwei/auto-ontology/blob/7cda81020e38d535239f5a5739db75099e878d79/frontend/prisma.config.ts#L22) | 实际 Prisma schema frontend |
| [auto_ontology/semantic/contracts/models.py:48](https://github.com/Leafhelpwei/auto-ontology/blob/7cda81020e38d535239f5a5739db75099e878d79/auto_ontology/semantic/contracts/models.py#L48) | 已有角色路径及 ne/not_in，勿重复制造第二套过滤 DSL |
| [auto_ontology/semantic/contracts/models.py:129](https://github.com/Leafhelpwei/auto-ontology/blob/7cda81020e38d535239f5a5739db75099e878d79/auto_ontology/semantic/contracts/models.py#L129) | 当前 Metric 字段和 required_filters |
| [auto_ontology/server/metrics/router.py:189](https://github.com/Leafhelpwei/auto-ontology/blob/7cda81020e38d535239f5a5739db75099e878d79/auto_ontology/server/metrics/router.py#L189) | 已有 revision 读取；需补差异/影响闭环 |
| [frontend/components/metricsPage/MetricsWorkspace.tsx:401](https://github.com/Leafhelpwei/auto-ontology/blob/7cda81020e38d535239f5a5739db75099e878d79/frontend/components/metricsPage/MetricsWorkspace.tsx#L401) | 已有版本选择不等于完整差异分析 |
| [docs/plans/adr-001-governed-model-snapshots.md:19](https://github.com/Leafhelpwei/auto-ontology/blob/7cda81020e38d535239f5a5739db75099e878d79/docs/plans/adr-001-governed-model-snapshots.md#L19) | 已采用模型快照作为认证/发布单元 |

## 附录 C. 接手前最后一次自检

- [ ] 我拿到的是修复分支及其增量，不是旧 main 或已丢失补丁。
- [ ] 我没有把 60 个测试当成 60 项整改，也没有把已开发当全部环境验收。
- [ ] 我已保留最新 7cda810 中的确认、澄清与解释视图修复。
- [ ] 我覆盖了 15 项未完开发、Auto、差异/影响、排除规则以及验收缺口。
- [ ] 我了解 public/frontend/vdb 实际归属、MySQL 5.7 真实测试及现有快照 ADR。
- [ ] 我有可运行的本地金标准和失败注入测试，并将外部凭据缺口单列。
- [ ] 我每批保存源码和证据；不使用曾经丢失的测试结论宣称当前版本完成。
- [ ] 我会在完成一个批次后继续下一批，直到达到上述完成标准或将确实外部阻塞的剩余项完整交付。

## 附录 D. 60 项原始验收条件逐条保留

以下为原审计的验收条件全文索引，便于接手者仅依靠本 MD 和源码核对覆盖。它们描述要求而非当前结果；新实现已扩展的范围（例如受限跨事实）应增加对应金标准，不能只按旧版拒绝场景验收。原始证据、触发条件及建议仍可查仓库 original-findings.json。

| 编号 | 原始验收条件 |
| --- | --- |
| RUNTIME-01 | 测试A异常后B仍完整返回B结果；C只能收到C事件。分别覆盖explore/metrics、不同user_id、不同subject_token、error与done到达间隔、worker异常退出。 |
| RUNTIME-02 | 双副本随机分流下，同会话仅一个run被接受；另一副本可watch与cancel；杀死owner后任务进入可解释终态/被安全接管，不永久running。 |
| RUNTIME-03 | 并发提交超过上限时PID数和连接数稳定在配置预算内；相同会话重复请求快速409且不增加worker；队列超时/取消释放配额。 |
| RUNTIME-04 | 在READY前强杀子进程、卡住初始化以及发送ERROR三种情况下，acquire在配置时间内退出、没有孤立线程/进程，并可创建下一健康worker。 |
| RUNTIME-05 | 百万行和单行超大文本fixture下峰值RSS受控；截断可见且不会被LLM描述成全集；取消关闭游标与事务；小结果行为保持。 |
| RUNTIME-06 | 阻塞旧worker，在连接更新后放行其完成，下一请求必须使用新generation；旧worker不能抢占新standby；覆盖删除连接与凭据轮换。 |
| RUNTIME-07 | 202前任务已持久化；202后SIGKILL并重启能恢复或明确标记中断；重试不重复产生数据；UI区分idle/running/unreachable/failed/interrupted。 |
| RUNTIME-08 | 同时触发同源两次ingest及delete，只有合法串行顺序执行；双副本不重复领取同一job；失去lease的旧worker不能继续发布；不同源仍可并行。 |
| RUNTIME-09 | 第二chunk抛错、部分embedding为None、写入中进程被杀时，检索仍看到完整旧generation；全成功后一次切换到完整新generation，不出现混合或部分可见。 |
| RUNTIME-10 | 注入300ms数据库等待时并发liveness/ticker仍按低延迟执行；数据库长锁等待在配置预算内结束；线程池饱和时可观察且不阻塞事件循环。 |
| SEC-01 | 本地 banned=true 的用户用有效 SSO JWT访问 catalog、metrics、chat、connections API 均返回401/403。；解封后恢复；API token、OAuth token、cookie 分支的封禁/降权回归均通过。 |
| SEC-02 | 本地签名测试中，正确issuer但无关aud、缺exp、错误provider subject均被拒绝。；预先配置的 Auto Ontology 和 AI-Q audience可继续使用，正常已绑定SSO用户兼容。；不同subject但同email的token不能静默取得已有用户权限。 |
| SEC-03 | 默认Compose从另一主机无法连通3001/3002，frontend业务请求仍正常。；Helm服务维持ClusterIP，NetworkPolicy限定入口；网关伪造header回归持续拒绝。 |
| SEC-04 | 带fake access_token_override的连接序列化及GET均不包含token。；外部写入该内部字段返回422；运行时SSO交换仍可内部使用且不写回存储。；对password、private_key、truststore和未知秘密字段执行回归。 |
| SEC-05 | 实际SQLValidationAgent对UPDATE/DELETE/写CTE/多语句返回拒绝，connector spy确认未发往数据库。；隔离PG中使用具备写权限的测试账号，探索执行仍由READ ONLY阻止写入且表内容不变。；正常SELECT与现有metrics只读、取消、超时回归通过。 |
| SEC-06 | 含ssl_verify_cert/identity与CA的URI能生成准确driver kwargs。；在隔离TLS MySQL中，错误CA/错误主机名/不提供TLS在严格模式下一律失败，正确证书成功。；同一TLS配置用于read_transaction、legacy execute和cancel连接。 |
| SEC-07 | 合成PII标签邮箱在无PII读取授权时不进入sample/embedding/LLM请求和返回结果。；允许读取的授权主体与汇总指标正常；历史敏感样本及embedding可重建/清除。；文档和UI清楚说明分类开关不自动脱敏。 |
| SEC-08 | 双用户/双租户用同名业务对象验证列表、详情、搜索、导出、Chat、compile/execute及缓存都不跨界。；请求参数中的zone_ids/model_id不能扩大授权范围；数据库RLS在应用过滤漏掉时仍有效。；现有全局共享模式作为明确配置保留，不暗改已有用户可见性。 |
| SEM-01 | 加入 A→B→A 的真实结果回归，模型或查询应以币种/路径错误拒绝；原有非循环币种维度与单币种过滤保持正确，不能只比较字段 ID。 |
| SEM-02 | 用真实 PG 运行上述 2^53 边界数据，认证/执行必须拒绝不兼容键，正常相同类型与经过证明的无损键继续通过；增加不同 collation/opclass 的边界矩阵。 |
| SEM-03 | 完整服务回归：生成表达式修改后必须 SOURCE_CHANGED、禁止 trusted 结果；对普通列无关注释变更不误伤。测试旧指纹迁移和重新认证流程。 |
| SEM-04 | 真实 PG SUM(50000*50000)=2500000000，组合表达式、负数与NULL行为保持；MySQL unsigned减法、DECIMAL最大精度用独立金标准验证。 |
| SEM-05 | SQL NULL、float NaN/±Infinity、Decimal NaN/Infinity分别验证；只有真正NULL编码为null，异常不得被标成正常trusted数值结果，历史/SSE一致。 |
| SEM-06 | 上述UTC源/Paris分组成功；将end延长1微秒后仍按不支持跨转换规则拒绝。补秋季回拨及源DATETIME墙上时间边界测试，不能放松模糊存储语义。 |
| SEM-07 | 买方Alice/卖方Alice分别生成对应alias条件；两者同时过滤可组合；未指定角色仍拒绝。PG和MySQL均与独立金标准对照。 |
| SEM-08 | 一订单多次支付、跨币种、缺失支付、不同业务时间角色、零分母均有金标准；不得在明细级直接join多个fact。MySQL5.7 SQL生成/执行与许可证/适配器可用性需独立PoC，不能假定MetricFlow可直接替换。 |
| SEM-09 | 变更未引用source不会破坏无关指标；变更实际依赖一定拒绝；任何fallback必须满足原业务语义和显式优先级，不能因错误自动跳到不等价数据。另做大模型查询锁/连接数基准。 |
| LLM-01 | 六轮澄清、累计字符上限、长回答三类测试中，原客户排除/币种条件必须保留或明确中止；不得静默扩大查询范围。 |
| LLM-02 | stub 返回 None 和抛异常时均不得产生正常 valid_sql 路由；用户问题含客户/状态条件而候选遗漏时，模型不可用不能越过该语义检查。 |
| LLM-03 | 对问题/evidence/列描述/SQL 示例分别放入恶意哨兵，捕获消息确保其不被提升为系统政策；模型对抗评测不得因这些文本移除用户显式条件或扩大权限。 |
| LLM-04 | 固定错误候选覆盖“昨天→全部历史”“支付→确认”“排除条件消失”；应澄清、拒绝或要求确认，不能自动当作语义正确执行。 |
| LLM-05 | 单指标请求中捕获的提示不包含无关指标私密证据哨兵；候选检索召回和歧义识别通过固定评测；记录实际 token 与成本。 |
| LLM-06 | caplog 在 INFO 级捕获 Chat、explore、intent 和错误路径时，合成敏感哨兵不出现；仍能依 ID 排查问题。 |
| LLM-07 | None×3 能触发允许的 fallback；invalid dict→valid dict 发生两次调用并成功；真正拒绝不可通过无条件切换模型规避；最终失败保留明确原因。 |
| LLM-08 | 永久 schema/auth 错误一次终止；瞬时故障总次数遵守同一个预算；连续 None 的节点不可产生 3×3 重试；取消/预算耗尽必须停止后续 LLM 调用。 |
| LLM-09 | 合成 policy/auth 拒绝不得调用 fallback；允许的 503/超时可在同数据等级路由中降级；响应或审计元数据能追溯实际模型且不泄露 key。 |
| LLM-10 | 对“那上个月呢？”注入模型 None/异常，返回明确 unavailable/clarification，不能丢弃原客户/口径条件后继续生成；真正独立新问题仍保持原样。 |
| LLM-11 | 构造函数测试验证普通模型收到 0.0、不支持模型不发送且有明确说明；网关别名不应仅靠字符串前缀猜能力。真实能力烟测应在有授权模型 key 时单独进行。 |
| FE-01 | 用可控 Promise 延迟 A 的治理请求；切 B 编辑后完成 A，B 内容和 dirty 保持，A 缓存正确更新。；同一模型正常完成后更新版本；切页后返回不会回放陈旧导航。 |
| FE-02 | Windows 150% 显示缩放下，用微软拼音在 Edge/Chrome 完成候选词，确认期间不发送；下一次独立 Enter 只发送一次。；单元事件序列覆盖 compositionstart、Enter、compositionend、普通 Enter、Shift+Enter。 |
| FE-03 | 延迟 A 的创建/拒绝响应，切 B 并等待，B 不出现 A 内容；B 自有草稿不被清空。；留在 A 的普通发送拒绝仍恢复问题。 |
| FE-04 | 延迟 A 测试，编辑为 B 后 A 成功不得启用 Save/Next，也不得写 A schemas。；重新测试 B 成功后才能保存 B，失败时保留具体错误。 |
| FE-05 | 来源/字段/维度/关系/指标各执行删中间项→新增，ID 无重复。；被引用 ID 重命名/删除后无悬空引用，或明确阻止并显示依赖清单。 |
| FE-06 | 表单和未应用 JSON 在导航/后退/刷新时均提示或可恢复；保存/丢弃后不再误报。；恢复遇到服务端版本变化时保留本地内容并提示冲突，不静默覆盖。 |
| FE-07 | 1280×720 下40个口径（含长中文名）展开时输入/停止按钮始终可见且可键盘到达，消息仍可滚动；无页面水平溢出。；复测实际 innerWidth/innerHeight 为1280×720、1440×900、1920×1080、2560×1440和3840×2160，另测浏览器200%缩放；不要把显示器英寸当断点。 |
| FE-08 | 在目标笔记本用10k×30真实长度数据记录首次可交互、主线程long task与滚动；翻页只渲染当前页，金额字符串逐字相同。；500节点+20次扩展压测，记录节点/边数与P95交互延迟；选择的性能门槛写入测试而非凭感觉。 |
| FE-09 | 仅用键盘完成连接向导，焦点不越出弹窗；Escape关闭后回到触发按钮。；NVDA+Edge朗读对话框标题，自动a11y检查无dialog-name问题；嵌套弹窗焦点与滚动锁正确。 |
| FE-10 | 0、1.5、10001和非法时间在字段旁显示具体原因并阻止发送，1和10000正常。；任意FastAPI422 detail数组保留loc/msg并定位到对应字段；API契约变化能触发生成差异或fixture一致性测试失败。 |
| ENG-01 | 对旧版数据库快照执行升级：记录数与可归属信息保留，无法归属的记录进入归档；重复执行幂等；回滚/前滚方案经演练。 |
| ENG-02 | 在开发分支提交最小变更可看到对应 commit 的 Python、前端、OpenAPI checks；失败阻止合并。 |
| ENG-03 | 故意破坏一条 Chat/指标行为测试使 CI 失败；E2E 至少覆盖登录权限、澄清追问、取消恢复和 1280×720 布局。 |
| ENG-04 | 故意使项目声明与锁文件不一致时构建失败；同 commit 两次构建比较依赖清单一致。 |
| ENG-05 | 仅安装核心加 mysql extra 可启动并查询；未安装的 connector 不影响服务启动；比较镜像体积和冷启动实测。 |
| ENG-06 | 同一 run 能串起网关、worker、LLM、SQL；可定位 p95 延迟和单请求 token；超预算告警不依赖明文问题日志。 |
| ENG-07 | 从备份恢复认证模型、用户会话和向量数据；测试数据库故障切换，并记录可达成的 RPO/RTO。 |
| ENG-08 | 许可证检查通过，完整测试不再有此已知失败。 |
| ENG-09 | 一个 workflow 只执行一次全量 suite，跳过数量和失败数量来自同一 run，意外跳过仍阻止 CI。 |
| FE-11 | 业务人员无需查看原始 UUID 即可完成订单金额模型；选择失效或无权访问的对象必须明确报错。 |
| FE-12 | 仅用键盘可搜索、选择、展开并查看任意已加载实体；屏幕阅读器能读到名称、类型和关系方向。 |
| FE-13 | 合成密码前后含空格、换行和非 ASCII 字符时，到 driver 的值逐字一致；普通主机名仍按规则规范化。 |
