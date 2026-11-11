<!--
SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# 语义层增强实施记录

- 实施日期：2026-11-11
- 上游：NVIDIA/auto-ontology
- 基线：`fc43a82600e18b19d159c52023f54f5572f537ac`
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
| PostgreSQL 结果级回归 | 已通过 | `auto_ontology/semantic/contracts/tests/`、`auto_ontology/server/metrics/tests/` |

## 已确认的环境

- Python 3.12，使用 `uv.lock` 安装依赖；可选 Kumo 依赖用于运行现有完整测试套件。
- Node.js 24，使用项目指定 pnpm 10.34.3 及冻结锁文件安装前端依赖。
- 隔离 PostgreSQL 17.11 与 pgvector 0.8.0；仅使用本地测试数据，不连接用户生产系统。
- GitHub 个人 fork 为 `Leafhelpwei/auto-ontology`；开发分支为 `feat/governed-metrics`。

## 验证记录

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

未进行外部 LLM 实时调用、登录浏览器端到端测试、生产数据联调、容器镜像构建或 Helm 实际集群部署。Next 构建成功，但本地未配置完整 Auth 数据库产生连接/BetterAuth 配置警告。新增运行开关保持默认关闭。

## 交付与继续开发

代码位于 `feat/governed-metrics`，从上述上游基线增量开发；数据库迁移为 `7d4a8e2c91b0`，新增 15 个后端接口。使用说明见 [指标工作台](../governed-metrics.md)。

本地实现和验证已完成，交付分支为 [Leafhelpwei/auto-ontology · feat/governed-metrics](https://github.com/Leafhelpwei/auto-ontology/tree/feat/governed-metrics)。该分支承载本次开发；`main` 保留上游基线。没有向 NVIDIA 上游提交 PR，也没有执行生产发布。

## 后续范围

- 原始 Chat 会话中的标准指标模式、会话追问及持久化整合。
- 指标级独立修订发布与跨模型固定版本依赖。
- 自动从历史 SQL 完整推导业务可认证指标。
- 多事实对齐、预聚合改写、更多数据库方言。
- 真实企业口径认证及生产启用。

这些后续项不影响首版工作台使用受治理的结构化请求与自然语言解析，但不应宣称本规划所有阶段均已完整实现。
