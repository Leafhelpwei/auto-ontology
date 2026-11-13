<!--
SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# 指标工作台：安装、使用与开发

本分支在 Auto Ontology 内增加 `/metrics` 工作台，将指标、来源和关系定义保存为版本化契约，并生成可解释、带参数的 PostgreSQL 查询。设计参考 Ontology Playground；数据接入、权限、存储和执行继续使用 Auto Ontology。

## 启用

先获取本次开发分支；NVIDIA 上游 `main` 不包含本 fork 的指标治理和 Chat 集成扩展：

```bash
git clone --branch feat/governed-metrics https://github.com/Leafhelpwei/auto-ontology.git
cd auto-ontology
```

已有此 fork 的本地工作区可执行 `git fetch origin` 后切换到 `feat/governed-metrics`；切换前先保存自己的未提交修改。

1. 按仓库现有说明安装后端和前端依赖，配置应用元数据库与 PostgreSQL 业务数据源。
2. 备份元数据后执行新增迁移：

   ```bash
   uv run alembic upgrade head
   uv run alembic check
   ```

   Chat 的模式、模型版本、澄清和查询依据保存在 `frontend` 模式的消息 `query_context` 字段中。还需同步 Prisma schema：在 `frontend` 目录、使用与应用一致的数据库配置运行 `sh prisma/migrate.sh` 和 `pnpm db:generate`。Docker Compose 的 `frontend-migrate` 服务、Helm 的同名迁移 hook 沿用这一同步流程；只运行 Alembic 不会更新前端会话表。

3. 指标运行时默认开启，无需额外设置。沿用旧版配置时，若 `AUTO_ONTOLOGY_GOVERNED_METRICS_ENABLED=false`，需改为 `true` 或移除该设置；显式关闭配置仍然生效。修改环境变量后，直接运行的后端需重启；Docker Compose 首次按通用说明运行 `docker compose up -d --build`。已有本分支镜像时运行 `docker compose up -d --no-deps --force-recreate auto-ontology` 重新加载环境变量，普通 `docker compose restart` 不会读取新的 `.env`。Helm 的 `backend.governedMetricsEnabled` 默认 `true`，同时需使用本分支构建的镜像，不能只升级 chart 而继续运行旧镜像。
4. 使用现有登录方式进入 Auto Ontology，在导航中打开 Metrics 或访问 `/metrics`。

如需停用，设置 `AUTO_ONTOLOGY_GOVERNED_METRICS_ENABLED=false`，或 Helm `backend.governedMetricsEnabled=false`。关闭时仍可管理模型，编译、草稿预览、自然语言解析和查询执行返回 `FEATURE_DISABLED`，模型和修订版不会删除。默认开启不跳过登录、权限、认证、激活或来源结构校验。

新增表为 `governed_model`、`governed_model_revision`、`governed_model_certification`。它们属于现有 public 元数据模式。发布前应在隔离数据库验证迁移；降级会删除这些新增治理数据，日常回滚优先关闭开关并保留数据。

## 建立一个可查询模型

1. 先通过现有数据源功能完成 Catalog 接入，创建对应的业务 Term。
2. 新建模型。模型 ID 使用 1–128 个英文字母、数字、下划线、点或连字符，以字母/数字开头；展示名称可用中文。在来源编辑器中填写真实 `datasource_id`、`catalog_table_id`、`owner_term_id`、schema/table 名称与字段的 `catalog_column_id`。名称必须与这些 ID 指向的资产一致。
3. 定义完整行粒度。粒度必须有真实唯一键支持；定义关系时列出全部复合键，声明方向、业务角色、基数和 INNER/LEFT 语义。
4. 添加原子指标：聚合方式、表达式 AST、必带过滤、允许维度、时间字段/角色和单位。金额须声明币种字段或固定币种。复合指标引用同一快照内的原子指标。
5. 保存草稿，查看校验报告并修复问题。结构校验通过后，提交业务审核和结果核对依据以认证该修订版，再激活。
6. 选择指标、维度和明确时间范围，先查看查询依据，再执行。

编辑表单和高级 JSON 编辑共同操作同一模型。复杂表达式与谓词使用结构化 JSON，不能填入任意 SQL。数据库和指标定义变更必须重新验证；自动测试通过不等于企业指标已被业务方认证。

### 示例与结果预期

[订单验收模型](../auto_ontology/semantic/contracts/tests/example_model.json) 展示订单明细、订单、客户和产品关系，以及已确认金额、已取消金额和已确认订单数。该文件中的 Catalog ID 是测试占位符，导入真实环境前必须替换为已接入资产的 ID；它不是预认证的生产配置。

对应的 PostgreSQL 测试数据和结果断言位于 [契约测试](../auto_ontology/semantic/contracts/tests/)。其中：

| 查询 | 预期 |
| --- | --- |
| 已确认金额，按币种 | CNY 250、USD 100 |
| 已取消金额，按币种 | CNY 50；USD 无匹配金额为 NULL |
| 已确认订单去重数 | 2 |
| 金额不分币种且未限定单一币种 | 拒绝 |
| 用订单确认状态回答“已支付金额” | 不能据此确认支付事实 |

## 修订版与认证

模型内的 metric/source/relationship 保有稳定 ID，整个模型快照是首版认证与发布单元。每次保存修改产生新修订版；旧版定义不被覆盖。创建草稿不撤销仍有效的已发布版本。

状态流转为 `draft → validated → certified`；`active_revision` 指向用于标准查询的已认证版本。弃用当前版本会清除激活指针。写操作携带 `expected_version`，过期编辑返回 `409 VERSION_CONFLICT`，不会覆盖其他人的修改。

认证时服务端记录审核者、证据、模型指纹和来源结构指纹。标准查询会重新核对真实 Catalog 和 PostgreSQL 列/唯一索引结构；不匹配时返回 `SOURCE_CHANGED`。认证只覆盖该快照，后续编辑必须重新校验认证。

草稿预览需要编辑权限，只生成计划并标记 `trusted=false`，不能经标准执行接口运行。正式执行只使用当前激活且认证有效的版本，并在服务端重新编译，客户端无法提交自制 SQL 或替换计划。

原有 SqlAttribute 的 SQL、业务说明或 Term 关联发生变化时清除旧认证。历史 SQL 提炼保存完整查询；复杂历史查询可作为待审核候选，不自动转换成受限编译器可执行的指标。

## 工作台自然语言查询

在已激活模型的查询区域输入问题，可先选择具体指标口径，再点击解析。自然语言解释复用现有 LLM 配置；无模型密钥时仍可使用结构化查询。

解释结果只有三类：

- `ready`：返回经共享编译器校验的结构化请求和计划，用户再执行。
- `clarification`：需要补充日期、时间角色、时区或指标口径；指标族歧义会返回可选口径。
- `rejected`：不支持、定义不一致或模型服务不可用；不会自动进入自由 SQL 流程。

## 在 Chat 中使用标准指标

Chat 的标准指标模式使用所选的已认证、已激活模型，和工作台共享解释、校验、编译与执行服务。解析为 `ready` 后在同一轮执行查询；`clarification`、`rejected` 或错误不会执行探索性 SQL。探索模式仍使用原有查询流程，用户需要显式切换模式。

1. 在 Chat 选择 **Standard metrics**，在 **Activated metric model** 中选取已激活模型。没有可选模型时，先在 Metrics 完成校验、认证与激活。
2. 输入指标问题。可在 **Metric variants (optional)** 中明确指标口径，也可留空让解释器识别；有歧义时仍须澄清。
3. 按提示在同一会话中回答澄清，或基于已执行请求继续追问。点击澄清中的口径选项只更新选择；在输入区补充条件后发送才会继续查询。结果显示模型版本、指标、实际查询和口径依据。
4. 模型已切换到新激活版本时，显式选择当前版本并重置指标上下文；历史回答仍保存其原版本。切换到 **Explore data** 才会使用探索性查询路径。

同一会话中的澄清回答和追问从服务端保存的结构化请求恢复上下文，绑定具体模型修订版与指纹。模型切换或版本失效不能沿用旧认证；查询仍会重新校验激活状态和来源结构。没有真实 LLM 配置时，自然语言解析不可用，可在工作台使用结构化查询。

## API 摘要

通过现有已认证 Next.js `/api` 网关访问。FastAPI 后端内部身份头由网关写入，不能将其当成公网认证方案；部署时继续保持后端私有网络边界。

| 方法和路径 | 作用 |
| --- | --- |
| `GET /api/metrics/config` | 运行开关 |
| `GET/POST /api/metrics/models` | 列表、创建草稿 |
| `GET /api/metrics/models/{id}` | 模型及修订历史 |
| `PUT /api/metrics/models/{id}/draft` | 新草稿 `{model, expected_version}` |
| `POST .../{id}/validate` | 校验 `{revision, expected_version}` |
| `POST .../{id}/certify` | 认证，另外提交 `evidence` |
| `POST .../{id}/activate` | 激活认证版本 |
| `POST .../{id}/deprecate` | 弃用版本 |
| `DELETE .../{id}?expected_version=N` | 归档模型，保留历史 |
| `POST .../{id}/compile` | `{revision, query, preview}` |
| `POST .../{id}/execute` | 标准执行 `{revision, query}` |
| `POST .../{id}/interpret` | `{revision, question, selected_metric_ids}` |
| `GET .../{id}/export?revision=N` | 导出原生快照和来源信息 |
| `POST /api/metrics/import` | 导入原生包为新草稿 |
| `POST /api/chat/completions` | SSE 会话入口，`query_mode="metrics"` 与 `metric_context` 选择标准指标模式 |

`/metrics` 接口的成功响应使用 `{ "data": ... }`。语义错误返回结构化 `code/message/path`，前端保留具体原因；完整规范见 [后端 OpenAPI](openapi/backend.json)。

Chat 入口沿用 SSE 响应，并将 `query_context` 随结果及会话历史返回。标准指标请求包含 `question`、`query_mode="metrics"` 和 `metric_context={model_id, revision?, selected_metric_ids}`；携带 `conversation_id` 才会保存并恢复该用户的会话上下文。客户端不能通过该字段提交可信查询、认证指纹或任意 SQL。未指定 `query_mode` 的 API 请求保持 `explore`，运行时默认开启不等于自动改写所有旧请求。

结构化查询示例：

```json
{
  "revision": 1,
  "query": {
    "metric_ids": ["confirmed_amount", "cancelled_amount"],
    "dimensions": ["currency_code"],
    "all_time": true,
    "limit": 1000
  }
}
```

时间查询使用 `time.start`、`time.end_exclusive`、`time.timezone`、`time.time_role`，可加 `granularity`。边界为 `[start, end_exclusive)`，不同时设置 `all_time=true`。查询返回 SQL、独立参数、实际来源、排除来源原因、过滤和关联依据、时间口径、模型与指标指纹。

首版 `datetime` 绑定要求 PostgreSQL `timestamp with time zone`（`timestamptz`）；不将无时区 timestamp 猜测为服务器或用户所在时区。`date` 字段的范围要求所选时区的午夜边界。

精确数值过滤和表达式常量可使用十进制字符串，例如 `"9007199254740993.01"`，服务端按已绑定字段类型解析并保留精度。结果中的 PostgreSQL NUMERIC 和超过 JavaScript 安全整数范围的值以字符串传输，前端不得再无条件转为 `Number`。`column_metadata` 给出列名、PostgreSQL 类型 OID 和 JSON 编码方式；空结果也保留列信息。

原生导出格式为 `auto-ontology-governed-model-v1`。导出的认证信息仅供追溯，普通导入总是草稿。导入已存在 ID 会报冲突，修改定义应使用新增草稿接口。现有 Ossie/RDF 导出不会自动携带本扩展，不能替代原生契约备份。

## 已支持的边界

- PostgreSQL；原子 `sum/count/count_distinct` 及受限四则表达式。
- 同一事实来源上的多个指标分别过滤、分别聚合，再按 NULL 安全的分组键对齐。
- 完整复合键；many-to-one/one-to-one；LEFT 关联的目标过滤保留在 ON 范围。
- 依赖指标先聚合再计算比值；零分母策略明确。
- 兼容性检查先于来源优先级；优先级相同且同样合格时拒绝歧义。

跨事实联合计算、自动预聚合改写、窗口/任意 SQL 表达式、未定义汇率的跨币种汇总和会放大事实行的关联仍不支持。不要仅通过改变基数标签来规避校验；认证会核对物理唯一约束。

## 本地验证

使用隔离测试 PostgreSQL 和仓库现有 fixture 环境，不对生产库运行测试：

```bash
uv run --no-sync pytest auto_ontology/semantic/contracts/tests auto_ontology/server/metrics/tests -q
uv run --no-sync ruff check auto_ontology/
uv run --no-sync ruff format --check auto_ontology/
uv run --no-sync python -m dev_tools.generate_backend_openapi
```

前端使用项目指定 pnpm 运行 Prettier、lint、类型检查和 build。具体执行结果、全量基线差异与仍未验证的部分记录在 [实施记录](plans/governed-metrics-progress.md)，不可将静态构建成功等同于生产联调完成。

本轮 Chat 集成验收使用真实本地登录、Next/FastAPI HTTP 和 PostgreSQL；只有模型服务使用显式 scripted 本地替身，覆盖澄清、追问、会话恢复、取消、权限、版本及来源变化。这验证了接口与治理流程，不代表真实 LLM 理解质量、浏览器 UI 或生产数据验收完成；正式自然语言使用仍需配置真实模型服务。
