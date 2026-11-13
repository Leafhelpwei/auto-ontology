<!--
SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# Auto Ontology

[English](./README.md) | **简体中文**

Agent 已经能够检索文档、查询数据库，但数据分析的价值取决于答案是否可靠：如果营收数字算错，回答再快也没有意义。Agent 需要一层上下文，说明数据在哪里、含义是什么、是否适用、如何使用、来自哪里，以及与哪些数据相关。NVIDIA Auto Ontology 是一个开放的库，可从企业元数据构建这一层上下文，协调不同系统中的业务定义，并通过 MCP、CLI 和 API 提供给 Agent。它可以与企业已有的语义模型配合使用。

它帮助开发者完成以下工作：

- **发现**：从元数据生成企业本体，接入已有本体和语义模型，构建供 AI 使用的知识图谱。
- **明确语义**：为指标、实体和关系提供可信的业务定义，协调不同系统中不一致的口径。
- **校验**：依据已认证的定义和血缘检查生成的查询，并在回答中提供来源信息。
- **开放接口**：通过 MCP、CLI 和 API 向不同 Agent 框架提供上下文。
- **持续改进**：将经过认证的修正和使用反馈纳入本体，逐步完善共享上下文。

> 本文是当前开发分支的中文说明。项目基于 [NVIDIA/auto-ontology](https://github.com/NVIDIA/auto-ontology)，并参考 Ontology Playground 的设计增加指标治理能力。下文的克隆命令指向本 fork 的 `feat/governed-metrics` 分支；模型默认值以本分支 [`.env.example`](./.env.example) 为准。

## 目录

- [评测](#评测)
- [主要功能](#主要功能)
- [新增指标治理扩展](#新增指标治理扩展)
- [软件组件](#软件组件)
- [快速开始](#快速开始)
- [工作原理](#工作原理)
- [接入你的 Agent](#接入你的-agent)
- [数据源、模型与预测](#数据源模型与预测)
- [集成与反馈](#集成与反馈)
- [参考文档](#参考文档)
- [许可证](#许可证)
- [声明与免责声明](#声明与免责声明)
- [安全](#安全)

## 评测

运行 BIRD 等 Text-to-SQL 评测，请参阅 [ontology SQL 评测仓库](https://github.com/NVIDIA/ontology-sql-eval/)。

> **许可证与贡献政策。** Auto Ontology 使用 [Apache License 2.0](./LICENSE)。项目打包、链接或使用的第三方开源组件列于 [`THIRD_PARTY_NOTICES.md`](./THIRD_PARTY_NOTICES.md)。**NVIDIA 上游项目目前不接受外部代码贡献。**

<p>
<img src="./docs/assets/arch.png" alt="Auto Ontology 架构" width="800">
</p>

## 主要功能

- **自然语言查询结构化数据**：将问题转换为 SQL，并在已连接的数据库上执行，支持 NVIDIA NIM 推理服务。
- **语义目录与本体层**：在关系型数据源之上管理业务语义，与数据目录一起存储在 PostgreSQL 中。
- **身份认证与 RBAC**：使用 [Better Auth](https://github.com/better-auth/better-auth) 实现认证和基于角色的访问控制。
- **Web 界面**：提供聊天、分析、数据目录、使用分析和设置页面。
- **灵活部署**：支持通过 Helm 部署到 Kubernetes，也可使用 Docker Compose 启动本地环境。

## 新增指标治理扩展

本开发分支新增原生 `/metrics` 指标工作台，沿用 Auto Ontology 的数据接入、权限、存储和执行体系，提供：

- **结构化定义**：定义指标、数据来源和关联关系，明确计算表达式、过滤条件、维度、时间口径、粒度和币种。
- **版本与认证**：保存不可变修订版，记录审核依据，经过校验、认证后激活。首版以整个模型快照为认证和发布单元。
- **确定性 PostgreSQL 查询**：从结构化请求生成参数化 SQL，返回来源、过滤、关联和指标口径等查询依据，并执行只读查询。
- **自然语言解析与澄清**：将问题解析为受约束的结构化请求；信息不足时要求澄清，解析成功后由用户执行。
- **历史 SQL 上下文保留**：保留候选 SQL 的完整查询上下文；修改 SQLAttribute 的语义内容后清除旧认证。
- **原生导入导出**：以专用格式导出模型快照，导入时创建新草稿。

运行开关默认关闭，在后端配置以下环境变量后启用：

```dotenv
AUTO_ONTOLOGY_GOVERNED_METRICS_ENABLED=true
```

关闭开关时仍可管理模型，但编译、草稿预览、自然语言解析和执行不可用。Docker Compose 修改 `.env` 后，已有本分支镜像的环境需重新创建后端容器以加载配置：

```bash
docker compose up -d --no-deps --force-recreate auto-ontology
```

普通 `docker compose restart` 不会加载新的环境变量。首次安装使用下文的完整构建流程；从旧版本升级时，应先按指南备份元数据、执行迁移，并构建本分支镜像。Helm 对应配置为 `backend.governedMetricsEnabled=true`。

标准指标查询目前在 `/metrics` 工作台内完成，尚未接入原有 Chat 的流式会话与历史记录。该扩展首版支持 PostgreSQL，不支持跨事实来源联合计算、自动预聚合改写或未定义汇率的跨币种汇总；不要将下文原有数据源连接能力等同于指标编译器的方言支持。

详见 [安装与使用指南](./docs/governed-metrics.md)、[模型快照设计决策](./docs/plans/adr-001-governed-model-snapshots.md) 和 [实施与验证记录](./docs/plans/governed-metrics-progress.md)。

## 软件组件

| 组件 | 技术 | 职责 | 默认端口 |
| --- | --- | --- | --- |
| 前端 | Next.js 16、React 19、TypeScript、Tailwind CSS 4、Better Auth、Prisma | Web 界面、身份认证、API 网关 | 3000 |
| 后端 | FastAPI、Python 3.12+、LangChain | 聊天与自然语言转 SQL、目录和数据源 API | 3001 |
| 数据接入服务 | FastAPI、Python 3.12+ | 接入表数据、写入向量、执行语义编译 | 3002 |
| MCP 服务 | FastMCP | 通过公开 API 提供只读 Agent 工具 | 3003 |
| PostgreSQL + pgvector | PostgreSQL 17 | 应用元数据、数据目录、本体和向量存储 | 5432 |
| HashiCorp Vault | 可选 | 安全存储连接凭据 | — |

### NVIDIA NIM

Auto Ontology 使用 NVIDIA NIM 端点进行推理，可以接入托管的 [NVIDIA API 目录](https://build.nvidia.com) 或自行部署 NIM。本分支 `.env.example` 中的模型配置为：

- **大语言模型**：`nvidia/nemotron-3.5-lightning-30b-a3b`
- **向量模型**：`nvidia/nemotron-3.5-embed-1b`
- **重排序模型**：`nvidia/nemotron-3.5-rerank-8b`

通过 `DEFAULT_MODELS_ENDPOINT`、`DEFAULT_MODELS_MODEL`、`EMBED_ENDPOINT`、`EMBED_MODEL` 等环境变量指定服务端点与模型，并配置相应 API 密钥。

### 模型配置三元组

每个模型角色由三个环境变量组成：`<PREFIX>_ENDPOINT`（服务 URL）、`<PREFIX>_API_KEY` 和 `<PREFIX>_MODEL`。

| 前缀 | 用途 |
| --- | --- |
| `DEFAULT_MODELS` | 共享默认配置，其他角色未设置的字段回退到对应默认值 |
| `REASONING` | 主要聊天和自然语言转 SQL 模型 |
| `NON_REASONING` | 实体提取等较轻量的 Agent 步骤 |
| `EMBED` | 文本向量模型，数据接入与查询阶段必须使用相同模型 |
| `RERANK` | 检索流程中的重排序模型 |

可以设置完整三元组，也可以只覆盖部分字段。未设置的字段会回退到对应的 `DEFAULT_MODELS_<FIELD>`。通常先配置 `DEFAULT_MODELS_*`，再为不同角色覆盖模型或端点即可。为兼容旧配置，`*_API_KEY` 还支持回退到 `NVIDIA_API_KEY`。

## 快速开始

以下流程使用 Docker Compose 启动 Auto Ontology，并加载微软的示例销售数据库 Wide World Importers。需要 Docker Compose v2、[uv](https://docs.astral.sh/uv/) 和从 [build.nvidia.com](https://build.nvidia.com) 获取的 NVIDIA API 密钥（`nvapi-...`）。

1. 克隆本 fork 的开发分支并创建 `.env`：

   ```bash
   git clone --branch feat/governed-metrics https://github.com/Leafhelpwei/auto-ontology.git
   cd auto-ontology
   cp .env.example .env
   ```

   在 `.env` 中设置以下值，替换全部占位符：

   ```dotenv
   DEFAULT_MODELS_API_KEY=nvapi-...
   AUTH_SECRET=<long-random-string>
   APP_URL=http://localhost:3000
   POSTGRES_PASSWORD=<new-postgres-password>
   CONNECTION_STRINGS=postgresql://postgres:<same-postgres-password>@postgres:5432/wideworldimporters
   AUTO_ONTOLOGY_ADMIN_PASSWORD=<new-admin-password>
   PGADMIN_PASSWORD=<new-pgadmin-password>
   ```

   `AUTH_SECRET` 可使用 `openssl rand -base64 32` 生成；连接串中的数据库密码必须与 `POSTGRES_PASSWORD` 一致。Compose 会将 PostgreSQL、pgAdmin 和 Web 应用端口映射到宿主机，启动前请替换默认密码。`.env.example` 还为各模型角色设置了密钥占位符：将所有 `nvapi-your-api-key-here` 替换为有效密钥，或者删除相应的角色密钥配置，使其回退到 `DEFAULT_MODELS_API_KEY`。

   如需同时使用新增指标工作台，在 `.env` 中将 `AUTO_ONTOLOGY_GOVERNED_METRICS_ENABLED` 改为 `true`。

2. 启动 PostgreSQL 并加载示例数据库。初始化脚本位于 `auto-ontology-eval`，需传入上一步设置的数据库密码：

   ```bash
   docker compose up -d --wait postgres
   git clone https://github.com/NVIDIA/auto-ontology-eval.git ../auto-ontology-eval
   (cd ../auto-ontology-eval && uv sync && cp .env.example .env && \
     POSTGRES_PASSWORD='<same-postgres-password>' \
     uv run python scripts/seed/seed_wwi.py --database-name wideworldimporters --drop)
   ```

   `--drop` 会删除并重建目标示例数据库，仅用于可重建的演示数据，不能对生产数据库执行。

3. 构建并启动其余服务：

   ```bash
   docker compose up -d --build
   ```

   Compose 的迁移服务会在应用启动前执行数据库迁移。

4. 打开 <http://localhost:3000>，使用 `.env` 中的 `AUTO_ONTOLOGY_ADMIN_EMAIL` 和 `AUTO_ONTOLOGY_ADMIN_PASSWORD` 登录。进入 **Settings → Semantic Compilation** 并启用语义编译。数据接入在启动时运行；开启编译后，完成时会显示 **Semantic compilation is ready**。

5. 在 **Chat** 中提问，例如：

   > 2015 年 6 月，宾夕法尼亚州的 USB food flash drive - sushi roll 产品营收是多少？

   Agent 会将营收、产品和州映射到本体中的业务术语，沿关系找到需要的表关联并执行查询。回答包含 SQL 和处理步骤，便于核对来源。原项目示例展示的结果为：

   ```text
   回答：2015 年 6 月，该产品在宾夕法尼亚州的营收为 588.80 美元，来自 3 条发票明细。

   SQL： SELECT SUM(il.extendedprice) AS revenue, COUNT(*) AS invoice_lines
         FROM sales.invoicelines il
         JOIN sales.invoices i             ON il.invoiceid = i.invoiceid
         JOIN warehouse.stockitems si      ON il.stockitemid = si.stockitemid
         JOIN sales.customers c            ON i.customerid = c.customerid
         JOIN application.cities ct        ON c.deliverycityid = ct.cityid
         JOIN application.stateprovinces sp ON ct.stateprovinceid = sp.stateprovinceid
         WHERE si.stockitemname = 'USB food flash drive - sushi roll'
           AND sp.stateprovincename = 'Pennsylvania'
           AND i.invoicedate >= '2015-06-01' AND i.invoicedate < '2015-07-01'

   步骤：解析 Invoice Line（营收）、Stock Item、State Province、Invoice 等术语；
         关联发票明细 → 发票 → 客户 → 城市 → 州；
         根据实际数据校验过滤值，然后执行。
   ```

6. 再提出一个数据无法回答的问题。Wide World Importers 不包含广告数据，因此无法解析广告支出。通过 [MCP](#mcp-服务) 接入的 Agent 可先检查问题是否可回答，再请求澄清：

   ```text
   用户：2015 年 6 月，这款寿司卷 U 盘的广告支出回报率是多少？
   Agent：check_answerable → coverage 0.5；没有术语覆盖“广告支出”。
   Agent：可以查到该产品的营收，但当前数据没有广告支出，无法计算广告支出回报率。
          你希望先查看 2015 年 6 月的营收，还是接入记录广告支出的其他数据源？
   ```

上述问答与数字是原项目的说明示例，实际回答取决于已接入数据及配置。新增指标工作台还需按 [使用指南](./docs/governed-metrics.md) 建立模型、校验、认证并激活，示例数据库的语义编译不会自动生成已认证指标。

若重启后页面可以打开但左侧导航消失，可能是浏览器保留了过期会话 Cookie。清除 `localhost:3000` 的站点数据并重新登录。其他安装问题可查看 [安装排障指南](./skills/nvidia-ontology-setup/references/troubleshooting.md)。

## 工作原理

<p>
<img src="./docs/assets/arch.png" alt="Auto Ontology 架构图" width="800">
</p>

Auto Ontology 的基本流程分为三个阶段：生成本体、人工认证、由 Agent 根据本体回答问题。

本体主要保存三类定义：

- **业务术语（Terms）**：例如客户、发票明细，包含描述、同义词以及对应的数据列。
- **SQL 属性（SQL attributes）**：挂在术语下的、经过审核的 SQL 表达式，例如利润率或滚动累计值。Agent 可以复用它们，保持计算口径一致。
- **关系（Relationships）**：描述表之间如何关联，包括桥接表关系，可根据键和数据推断。

### 生成

数据接入服务从源数据库构建初稿：

1. 读取数据源：提取表结构、分析列特征、采样值，写入数据目录。
2. 识别业务实体：逐表提取术语，并映射到数据列。
3. 解析关系：推断表与桥接表之间的关联，从历史查询中建议 SQL 属性。
4. 写入本体：将术语、关系和 SQL 属性保存到 PostgreSQL，向量保存到 pgvector 用于检索。

生成的语义图可在 Web 界面中浏览。原项目的一次 Wide World Importers 编译示例生成了 31 个术语；选择 **Order** 可以查看其描述、相关术语、映射列和 SQL 属性：

<p>
<img src="./docs/assets/ontology-graph.png" alt="Wide World Importers 本体语义图，选中了 Order 术语" width="800">
</p>

### 认证

通过人工审核，使初稿成为可信的共享定义：

1. 在 Chat 中使用真实问题测试，或通过 MCP 调用 `check_answerable` 和 `ask_question`。
2. 在界面或 API 中完善术语、描述和 SQL 属性。
3. 确认内容正确后，认证名称、描述和 SQL 属性。

### 回答

针对每个问题，原有 Chat 流程会：

1. 分类问题意图，提取问题涉及的实体。
2. 通过向量检索找到候选术语、数据列和 SQL 属性。
3. 根据候选信息生成 SQL。
4. 在数据库中检查过滤值和查询，检查失败时修复 SQL。
5. 执行查询，返回答案、SQL 和处理步骤。

候选信息不足以支持查询时，Agent 会说明当前数据无法回答该问题。配置 KumoRFM 后，预测性问题会交给它处理，详见 [预测](#预测)。本分支新增的标准指标流程使用结构化契约和确定性编译器，具体规则见 [新增指标治理扩展](#新增指标治理扩展)。

Chat 中的回答可查看 SQL 和结果行、下载 CSV，或以图表展示：

<p>
<img src="./docs/assets/qa-example.png" alt="Chat 查询营收和客户数较高的产品，并展示 SQL 与营收图表" width="550">
</p>

Auto Ontology 兼容用于交换语义模型的开放标准 [Apache Ossie](https://ossie.apache.org/)。Ossie 提供 [Auto Ontology 转换器](https://github.com/apache/ossie-converters/tree/main/nvidia)，可导入和导出 Ossie YAML 格式的模型定义。入口为 **Settings → Import / Export**，或 `POST /api/model/import` 与 `POST /api/model/export`。

新增指标契约使用独立的原生导出格式；现有 Ossie/RDF 导出不会自动包含这些扩展，备份指标定义时应使用指标工作台的原生导出功能。

部署时将后端 3001 和数据接入服务 3002 端口限制在私有网络。FastAPI 信任前端转发的 `x-auto-ontology-user-id` 身份头；数据接入服务的 ingest、delete、reset、compile 等端点没有独立认证。客户端应通过 3000 端口的 Web 应用访问。

## 接入你的 Agent

### MCP 服务

[MCP](https://modelcontextprotocol.io) 服务向 MCP 客户端提供本体只读工具。每位用户通过 Auto Ontology 登录，工具调用按该用户的权限执行。

| 用途 | 工具 |
| --- | --- |
| 就绪检查 | `check_readiness`：检查当前部署是否已具备回答问题的条件 |
| 术语与结构 | `search_terms`、`get_term`、`get_term_columns`、`get_term_sql_attributes`、`get_sql_attribute`、`describe_table` |
| 可回答性检查 | `check_answerable`：检查本体是否覆盖问题所需的信息 |
| 问答 | `ask_question`：返回答案、SQL 和结果行 |

针对已运行的应用，启动本分支的 MCP 服务：

```bash
AUTO_ONTOLOGY_API_URL=http://localhost:3000 \
  uvx --from "git+https://github.com/Leafhelpwei/auto-ontology.git@feat/governed-metrics#subdirectory=mcp" auto-ontology-mcp
```

Cursor 配置文件 `~/.cursor/mcp.json`：

```json
{
  "mcpServers": {
    "auto-ontology": { "url": "http://localhost:3003/mcp" }
  }
}
```

Claude Desktop 的 `claude_desktop_config.json` 可通过 [`mcp-remote`](https://www.npmjs.com/package/mcp-remote) 接入：

```json
{
  "mcpServers": {
    "auto-ontology": {
      "command": "npx",
      "args": ["mcp-remote", "http://localhost:3003/mcp"]
    }
  }
}
```

修改配置后重启客户端，按提示登录。登录流程、配置和排障见 [MCP 文档](./docs/mcp.md)。上述 MCP 工具属于原有本体查询接口，新增指标工作台的接口见 [指标使用指南](./docs/governed-metrics.md)。

### Agent 技能

项目提供三项技能，指导编码 Agent 安装和使用 Auto Ontology。入口为 [AGENTS.md](./AGENTS.md)。

| 技能 | 内容 |
| --- | --- |
| [`nvidia-ontology-setup`](./skills/nvidia-ontology-setup/) | 安装、连接数据源、编译、验证服务就绪 |
| [`nvidia-ontology-management`](./skills/nvidia-ontology-management/) | 检查、建模、认证、导入导出与发布 |
| [`nvidia-ontology-query`](./skills/nvidia-ontology-query/) | 提问并校验 SQL、结果行和答案 |

### REST API

向 Web 应用的 `POST /api/chat/completions` 发送问题。先在用户菜单的 **API Tokens** 中生成 API 令牌。响应为 SSE（Server-Sent Events）流，`result` 事件携带答案，`data: [DONE]` 表示结束。

```bash
curl -N http://localhost:3000/api/chat/completions \
  -H "x-api-key: $AUTO_ONTOLOGY_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"question": "上周发出了多少笔订单？"}'
```

```python
import json
import os

import requests

response = requests.post(
    "http://localhost:3000/api/chat/completions",
    headers={"x-api-key": os.environ["AUTO_ONTOLOGY_API_TOKEN"]},
    json={"question": "上周发出了多少笔订单？"},
    stream=True,
    timeout=900,
)
response.raise_for_status()

for line in response.iter_lines(decode_unicode=True):
    if not line or not line.startswith("data:"):
        continue
    payload = line.removeprefix("data:").strip()
    if payload == "[DONE]":
        break
    event = json.loads(payload)
    if event["type"] == "result":
        print(event["answer"]["response"])
        print(event["answer"]["sql_code"])
    elif event["type"] == "error":
        raise RuntimeError(event["message"])
```

事件类型、多轮对话和网关信任边界见 [Conversation API 文档](./docs/conversation-api.md)。其他公开接口见 [OpenAPI 规范](./docs/openapi/auto-ontology-api.json)；新增指标 API 的摘要和后端规范入口见 [指标使用指南](./docs/governed-metrics.md#api-摘要)。

## 数据源、模型与预测

### 数据源

Auto Ontology 支持连接以下十种数据库或查询引擎：

- PostgreSQL
- MySQL
- Databricks
- Snowflake
- ClickHouse
- Trino
- Apache Kyuubi
- HeavyDB
- DuckDB
- SQLite

可在 **Settings → Connections** 中添加连接，也可通过 `CONNECTION_STRINGS` 配置。DuckDB 和 SQLite 仅支持连接字符串方式。URL 格式、数据范围与凭据存储见 [连接文档](./docs/connections.md)。新增指标编译与执行目前仅支持 PostgreSQL。

### 模型

Auto Ontology 使用四类模型角色。本分支示例配置使用 NVIDIA NIM：

| 角色 | 默认模型 | 用途 |
| --- | --- | --- |
| `REASONING` | `nvidia/nemotron-3.5-lightning-30b-a3b` | 构建术语、关系和 SQL 属性，生成查询与解释，评估可回答性 |
| `NON_REASONING` | `nvidia/nemotron-3.5-lightning-30b-a3b` | 问题意图、实体提取、候选选择，以及 SQL 检查与修复等较轻步骤 |
| `EMBED` | `nvidia/nemotron-3.5-embed-1b` | 为表、列、术语和 SQL 属性建立向量索引，匹配问题与相关对象 |
| `RERANK` | `nvidia/nemotron-3.5-rerank-8b` | 在编译桥接表 SQL 属性时，对候选表重新排序 |

接入自托管 NIM 或兼容 OpenAI 接口的服务时，设置 `<ROLE>_ENDPOINT`、`<ROLE>_API_KEY` 和 `<ROLE>_MODEL`。未设置的字段回退到 `DEFAULT_MODELS_<FIELD>`。所选模型应支持相关流程要求的结构化输出，并保持数据接入与查询使用相同的向量模型。全部变量见 [`.env.example`](./.env.example)。

### 预测

设置 `KUMO_RFM_API_KEY` 后，Agent 会将预测性问题交给 KumoRFM。KumoRFM 是托管服务，处理问题时会向该服务上传已连接的数据表，范围受以下配置限制：

- `KUMO_MAX_TABLES`：默认最多 20 张表。
- `KUMO_MAX_ROWS_PER_TABLE`：未设置时不限制每张表的行数。

启用前应确认将发送哪些数据，并完成所在组织的数据审核。

## 集成与反馈

如需与 NVIDIA 上游协作，将 Agent 平台、应用或存储服务集成到 Auto Ontology，可 [提交集成请求](https://github.com/NVIDIA/auto-ontology/issues/new?template=integration.yml)。

NVIDIA 上游团队维护原项目，目前不接受外部 Pull Request，但欢迎通过 Issue 提交集成请求和缺陷报告。安全漏洞报告方式见 [SECURITY.md](./SECURITY.md)，维护者工作流程见 [CONTRIBUTING.md](./CONTRIBUTING.md)。

## 参考文档

| 主题 | 文档 |
| --- | --- |
| 指标工作台安装与使用 | [docs/governed-metrics.md](./docs/governed-metrics.md) |
| 指标模型快照设计 | [设计决策](./docs/plans/adr-001-governed-model-snapshots.md) |
| 本分支实施与验证记录 | [实施记录](./docs/plans/governed-metrics-progress.md) |
| Helm / Kubernetes 部署 | [DEPLOYMENT.md](./DEPLOYMENT.md) |
| 数据连接、范围与 Vault | [docs/connections.md](./docs/connections.md) |
| 身份认证、SSO 与 API 令牌 | [docs/authentication.md](./docs/authentication.md) |
| 对话 API | [docs/conversation-api.md](./docs/conversation-api.md) |
| MCP 服务 | [docs/mcp.md](./docs/mcp.md) |
| REST API | [docs/openapi/auto-ontology-api.json](./docs/openapi/auto-ontology-api.json) |

## 许可证

Auto Ontology 使用 [Apache License, Version 2.0](./LICENSE)，SPDX 标识符为 `Apache-2.0`。

仓库中由 NVIDIA 编写的源文件包含以下形式的 SPDX 头：

```text
SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
All rights reserved.
SPDX-License-Identifier: Apache-2.0
```

项目使用的第三方开源组件、上游许可证和项目地址列于 [`THIRD_PARTY_NOTICES.md`](./THIRD_PARTY_NOTICES.md)。容器镜像引入的组件单独列于 [`CONTAINER_THIRD_PARTY_NOTICES.md`](./CONTAINER_THIRD_PARTY_NOTICES.md)。

## 声明与免责声明

本软件会自动检索、访问或与外部材料交互。这些材料不随本软件分发，而是受各自独立的条款、条件和许可证约束。用户需自行查找、审阅并遵守适用的条款、条件和许可证，并确认获取的材料在具体使用场景中的安全性、完整性和适用性。本软件按“原样”提供，不附带任何形式的保证。作者不对获取的外部材料作任何陈述或保证，也不对因使用或无法使用本软件及这些材料而产生的损失、损害、责任或法律后果承担责任。使用本软件及获取的材料所产生的风险由用户自行承担。

Auto Ontology 运行时可能交互的外部资源包括：

- 配置的模型服务端点，默认包括 NVIDIA API 目录（`integrate.api.nvidia.com`、`ai.api.nvidia.com`），适用各服务提供方与模型的条款。
- KumoRFM 服务，仅在设置 `KUMO_RFM_API_KEY` 时启用，适用 Kumo 的条款。
- 用户自己的源数据库，适用这些系统的相关协议。

许可证与原始声明文本请参阅 [LICENSE](./LICENSE) 和 [英文 README](./README.md#notice-and-disclaimer)。

## 安全

请按照 [SECURITY.md](./SECURITY.md) 中的说明报告安全漏洞。
