<!--
SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# 语义层增强实施记录

- 实施日期：2026-11-11
- 集成更新：2026-11-13，当前工作区本地运行与验证
- 上游：NVIDIA/auto-ontology
- 基线：`fc43a82600e18b19d159c52023f54f5572f537ac`
- 本轮集成起点：`a2332d8`
- 开发分支：`feat/governed-metrics`
- 目标：以 Auto Ontology 为主体，吸收 Ontology Playground 的指标契约、来源映射和解释交互。
- 设计变更：[ADR 001](adr-001-governed-model-snapshots.md)

## 当前范围

| 工作项 | 状态 | 实现位置 |
| --- | --- | --- |
| 历史 SQL 口径和关联上下文保留 | 已实现 | `auto_ontology/semantic/sql_attribute_suggester.py` |
| SQLAttribute 语义编辑清除旧认证 | 已实现 | `auto_ontology/server/sql_attributes/service.py` |
| 闭合契约、校验、参数化 PostgreSQL 编译 | 已实现 | `auto_ontology/semantic/contracts/` |
| 不可变修订版、认证、发布、导入导出 | 已实现 | `auto_ontology/dal/governed_models.py`、`auto_ontology/server/metrics/` |
| 自然语言转结构化请求与澄清 | 已实现；外部 LLM 待联调 | `auto_ontology/server/metrics/interpretation.py` |
| 原生指标管理与查询页面 | 已实现；登录浏览器联调待做 | `frontend/app/metrics/` |
| 默认开启运行时、显式关闭回退 | 已实现并通过默认值及 Helm 渲染验证 | `server/metrics/service.py`、`.env.example`、`helm/auto-ontology/` |
| Chat 标准指标模式、澄清续问与历史上下文 | 已接入；真实 LLM 与浏览器 UI 验收待做 | `server/chat/metrics_flow.py`、`frontend/components/chatPage/` |
| PostgreSQL 结果级回归 | 已通过 | `auto_ontology/semantic/contracts/tests/`、`auto_ontology/server/metrics/tests/` |

## 已确认的环境

- Python 3.12，使用 `uv.lock` 安装依赖；可选 Kumo 依赖用于运行现有完整测试套件。
- Node.js 24，使用项目指定 pnpm 10.34.3 及冻结锁文件安装前端依赖。
- 隔离 PostgreSQL 17.11 与 pgvector 0.8.0；仅使用本地测试数据，不连接用户生产系统。
- GitHub 个人 fork 为 `Leafhelpwei/auto-ontology`；开发分支为 `feat/governed-metrics`。

## 2026-11-11 首版验证记录

| 检查 | 结果 |
| --- | --- |
| 干净上游 `uv run --no-sync pytest auto_ontology dev_tools -q` | 2627 passed、2 skipped、1 failed |
| 最终冻结代码全量后端测试 | 2749 passed、2 skipped、1 failed；唯一失败与上游相同；比基线新增 122 项通过 |
| 最终核心契约及自然语言解释定向回归 | 67 passed（57 核心 + 10 解释）；真实 PostgreSQL |
| 历史 SQL、SqlAttribute 及 DAL 定向 PostgreSQL 回归 | 81 passed |
| 前端指标工作台 Vitest | 15 passed |
| `tsc --noEmit`、Next production build | 通过 |
| 前端 ESLint | 0 error；原有 UserMenu 警告仍在 |
| 前端全目录 `prettier --check .` | 通过 |
| 后端 Ruff lint / format | 通过 |
| Alembic 全量升级及 `alembic check` | 通过；无 schema drift；Helm revision 同步 |
| OpenAPI 与 DAL 公共接口快照 | 已重新生成；新接口纳入快照 |
| `git diff --check` | 通过 |

全量测试唯一失败为上游 `dev_tools/tests/test_licensing.py::test_every_source_file_has_an_spdx_header`：基线新加的 `CODE_OF_CONDUCT.md` 没有 SPDX 头。该文件未被本次修改；新增文件均通过同一检查。没有将此问题隐藏、跳过或伪报为全绿。

真实 PostgreSQL 覆盖金额独立过滤、NULL 分组、复合键、LEFT 关联目标过滤、比值先聚合、时间半开边界、来源能力选择、精确 Decimal/nullable bigint、空结果列、并发修订、结构漂移和只读事务。服务端使用绑定参数，客户端不提交可执行 SQL。

独立审查发现的金额序列化精度、无时区 timestamp、模型 ID 路由不可达、不同币种子指标组合等问题已纳入修复与回归。数值输入和返回值保留精确十进制字符串。

该次验证未进行外部 LLM 实时调用、登录浏览器端到端测试、生产数据联调、容器镜像构建或 Helm 实际集群部署。Next 构建成功，但当时本地未配置完整 Auth 数据库产生连接/BetterAuth 配置警告。首版运行开关当时默认关闭；当前默认值已按下述集成更新改为开启。

## 2026-11-13 本地集成验证

后端、`.env.example` 和 Helm 的受治理指标运行时均默认开启。未配置环境变量时启用；显式 `AUTO_ONTOLOGY_GOVERNED_METRICS_ENABLED=false` 或 `backend.governedMetricsEnabled=false` 仍可靠关闭。停用不会删除模型，开启仍须满足登录、权限、认证、激活和来源校验。

Chat 通过 `query_mode="metrics"` 接入标准指标，模型选择、澄清和追问复用共享服务；结果与历史保存 `query_context`，绑定版本与指纹。`Explore data` 保留原路径，标准模式拒绝后不会自动降级。新增会话字段由现有 Prisma schema 同步流程升级，Alembic 继续只管理 `public` 元数据模式。

真实 HTTP 联调发现旧 Catalog 接入未写入数据库 `dialect`，导致正常 PostgreSQL 来源在创建模型时被拒绝。本轮修复接入和重接入的方言持久化；旧空值记录仅在存在唯一、实际 PostgreSQL 连接时识别为 PostgreSQL，已知非 PostgreSQL 方言仍被拒绝。

| 检查 | 结果 |
| --- | --- |
| 本轮冻结代码全量后端测试 | 2812 passed、2 skipped、1 failed；唯一失败仍为原有 `CODE_OF_CONDUCT.md` 缺少 SPDX；比首版新增 63 项通过 |
| 本轮后端 Ruff lint / format | 通过；458 个文件检查无新增问题 |
| 本轮 OpenAPI 与 MCP 镜像 | 后端 72 paths / 97 operations，公开 API 94 paths / 134 operations，操作均有 schema；MCP 镜像逐字节一致 |
| 本轮前端 Vitest | 32 / 32 passed；比首版新增 17 项 |
| 本轮前端类型、格式、lint 与构建 | `tsc --noEmit`、全量 Prettier、Next production build 通过；ESLint 0 error，保留原有 UserMenu 警告 |
| 默认值、显式开关值及真实 PostgreSQL API 回归 | 14 passed；未配置返回 enabled=true，显式 false 返回 FEATURE_DISABLED |
| Catalog 方言持久化与旧数据兼容 | 19 passed：5 项旧绑定校验、14 项增量接入回归 |
| Helm 3.17.4 实际模板渲染 | 默认 true、显式 false、删除配置键后回退 true，均通过 |
| 本地 FastAPI / Next HTTP | 后端 health 200；Next 登录页 200；登录后 Catalog 与 `/metrics` 200 |
| 真实本地 Auth 会话 | 匿名 metrics/config 401；bootstrap admin 登录 200；会话角色为 admin；config enabled=true；登出后 401 |
| 已登录治理 HTTP 闭环与真实 PostgreSQL 结果 | 创建、校验、认证、激活后得到 2 笔订单、金额 350；版本失效和来源结构漂移被拒绝 |
| 显式关闭的全栈 HTTP 回归 | config enabled=false；模型治理读取仍为 200；execute/interpret 均返回 503 FEATURE_DISABLED |
| Chat 全链路 HTTP 集成 | 12 组通过：真实 Next 登录、FastAPI、PostgreSQL；自然语言模型使用显式 scripted 本地 HTTP 替身 |

Chat 集成覆盖治理发布、缺时间时澄清且不执行 SQL、回答全部时间后得到 2 笔订单/金额 350、基于原请求的 `query_update` 追问、新登录会话读取相同历史与查询依据、其他用户读/监听/取消/续问返回 404、注入查询或混用 `target_db` 返回 422、取消状态持久化为未受信结果、发布新版本后旧上下文返回 `NOT_ACTIVE`、显式选择新版本成功，以及来源结构漂移时拒绝且不回退探索查询。

以上检查使用本地真实 HTTP、Auth 会话与数据库；不是浏览器 UI 端到端验收。Chat 模型替身仅用于确定性集成验收，不是产品内置的无密钥回退。当前没有真实 LLM 密钥，尚未完成外部模型实时调用、生产数据联调或生产部署。Helm 模板渲染也不代表集群部署已验证。

本轮 Next production build 返回 0；构建环境未提供完整 Auth 数据库时仍出现既有 OAuthResource / BetterAuth 配置警告。真实 Auth 登录、会话读取、登出和权限检查已在另行启动的本地完整栈通过 HTTP 验证。

## 交付与继续开发

代码位于 `feat/governed-metrics`，从上述上游基线增量开发；数据库迁移为 `7d4a8e2c91b0`，新增 15 个后端接口。使用说明见 [指标工作台](../governed-metrics.md)。

2026-11-11 首版交付分支为 [Leafhelpwei/auto-ontology · feat/governed-metrics](https://github.com/Leafhelpwei/auto-ontology/tree/feat/governed-metrics)，`main` 保留上游基线。2026-11-13 集成更新按当前工作区本地运行方式验证；本地检查不代表远端 CI、真实模型或生产业务验收完成。没有向 NVIDIA 上游提交 PR，也没有执行生产发布。

## 后续范围

- Chat 的真实 LLM 调用与浏览器 UI 端到端验收。
- 指标级独立修订发布与跨模型固定版本依赖。
- 自动从历史 SQL 完整推导业务可认证指标。
- 多事实对齐、预聚合改写、更多数据库方言。
- 真实企业口径认证及生产启用。

这些后续项不影响首版工作台使用受治理的结构化请求与自然语言解析，但不应宣称本规划所有阶段均已完整实现。
