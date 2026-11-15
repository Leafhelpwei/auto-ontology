<!--
SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# MySQL 5.7 业务数据源

本分支可直接在 MySQL 5.7 业务数据库上执行标准指标查询，无需把业务表搬到 PostgreSQL。`/metrics` 工作台和 Chat 的 **Standard metrics** 使用同一套模型版本、认证、确定性编译与执行流程。原有 **Explore data** 保留探索查询流程；标准指标被拒绝时不会回退到自由 SQL。

应用元数据、Catalog、本体、pgvector、登录和会话仍保存在 PostgreSQL。`POSTGRES_*`、`DATABASE_URL` 和 Prisma/Alembic 配置继续指向内部 PostgreSQL；本指南中的 MySQL 配置只用于业务数据源。

## 连接与权限

1. 先按 [安装指南](governed-metrics.md#启用) 部署本分支并完成应用元数据库迁移。
2. 为业务数据库建立专用只读账号。以下由 DBA 执行，替换库名、允许的应用来源地址和密码；无需赋予写表、建表、文件、存储过程或全局管理权限：

   ```sql
   CREATE USER 'ontology_reader'@'APPLICATION_HOST' IDENTIFIED BY 'REPLACE_WITH_PASSWORD';
   GRANT SELECT ON `sales`.* TO 'ontology_reader'@'APPLICATION_HOST';
   ```

   可按实际范围改为逐表授予 SELECT。Catalog 与执行只访问账号可见的对象；需要查看绑定表的列、完整索引和存储引擎元数据。不要用管理员账号作为业务连接。

3. 在 **Settings → Connections** 添加 **MySQL**，填写 host、port（通常为 `3306`）、user、password 和 database（例如 `sales`），测试并保存。需要 `connection:manage` 权限。或者在部署环境设置：

   ```dotenv
   CONNECTION_STRINGS=mysql://ontology_reader:URL_ENCODED_PASSWORD@mysql-host:3306/sales
   ```

   URL 中的用户名、密码和数据库名里的保留字符需按 URL 规则编码。当前连接串解析 host、port、user、password 和 database，不将任意 URL 查询参数当作驱动选项。UI 管理与环境变量应选一种，避免 ingestion 与查询读取不同来源；规则及 Vault 存储见 [连接文档](connections.md)。

4. 使用现有 ingestion 流程抽取 Catalog，再建立对应的业务 Term。MySQL 连接只抽取所选 database 的表；指标来源的 `schema_name` 必须是这个数据库名，`datasource_id` 则填写 **Catalog database 的 ID**，不能填写连接名称或 URL。

标准查询另外要求 `catalog:read`；建模、校验、认证、激活和草稿预览需要 `catalog:edit`。Chat 继续要求 `chat:use`、会话写权限和所有权校验，同时复验标准指标权限。业务数据库 SELECT 权限与应用权限是两层独立控制。

## 在工作台建模，在现有 Chat 查询

1. 打开 **Metrics**，将 **New model / template engine** 设为 **MySQL 5.7**，点击 **New model**。也可下载所选方言的订单示例模板，再 **Import as draft**。模板含占位 Catalog ID，`sales` 也是示例数据库名；必须替换并校验，导入不会沿用导出包中的认证。
2. 在 **Sources** 绑定实际数据库、表、列与业务 Term。只支持 InnoDB 的真实 BASE TABLE；grain 必须由完整主键或唯一索引证明，复合键不能遗漏列，前缀唯一索引不能证明完整字段唯一性。所有来源须属于同一 MySQL 连接。
3. 配置指标表达式、必带过滤、允许维度、币种与时间角色。对于 `datetime` 字段，填写 **Stored datetime timezone**，见下节。修改已发布模型会保存为新草稿，不覆盖旧版本。
4. 保存 → **Validate** → 提交业务核对依据并 **Certify** → **Activate**。成功校验只证明契约和来源结构满足约束，认证依据仍须来自业务审核与结果核对。
5. 在工作台选指标、维度以及明确的时间范围或 all-time，查看计划后执行。没有 LLM 密钥也可走此结构化入口。
6. 需要自然语言时，进入原有 Chat，选择 **Standard metrics** 和已激活模型。指标口径可显式选择；缺少日期、口径或角色时先澄清。查询结果、版本、参数、来源依据、精确数值和列类型在流式回答与历史会话中保留。真实自然语言解析仍需配置可用模型服务。

示例结构化请求（可用于工作台生成的模型，指标和维度 ID 需与定义一致）：

```json
{
  "revision": 1,
  "query": {
    "metric_ids": ["confirmed_amount", "cancelled_amount"],
    "dimensions": ["currency_code"],
    "all_time": true,
    "limit": 100
  }
}
```

## 时间字段与查询边界

MySQL DATETIME 存的是没有时区信息的墙上时间；TIMESTAMP 根据会话时区与 UTC 转换。不要根据机器当前时区猜测业务时间。标准执行连接固定 `time_zone='+00:00'`，模型需要明确字段语义：

| 物理字段 | 模型 `data_type` | `datetime_timezone` | 规则 |
| --- | --- | --- | --- |
| DATE | `date` | 不填写 | 查询边界必须落在所选查询时区的午夜 |
| DATETIME / DATETIME(6) | `datetime` | 实际存储时区，例如 `UTC`、`+08:00` 或 `Asia/Shanghai` | 即使 all-time 查询也必须声明；不能把当地时间冒充 UTC |
| TIMESTAMP / TIMESTAMP(6) | `datetime` | `UTC` 或 `+00:00` | 服务端复核物理类型；非 UTC 声明会被拒绝 |
| PostgreSQL timestamptz | `datetime` | 不填写 | 保持既有 PostgreSQL 语义和历史指纹兼容 |

字段的存储时区允许 IANA 名称，或 `-12:59` 到 `+13:00` 范围内的 `±HH:MM`。查询的 `time.timezone` 仍使用 IANA 名称（包括 `UTC`），不能填数值偏移。`time.start` 与 `time.end_exclusive` 必须带 UTC 偏移，范围为 `[start, end_exclusive)`，且不能同时设置 `all_time=true`。

例如业务 DATETIME 确实以北京时间保存时，字段声明 `datetime_timezone="Asia/Shanghai"`；查询 2026 年 11 月 1 日可用：

```json
{
  "start": "2026-11-01T00:00:00+08:00",
  "end_exclusive": "2026-11-02T00:00:00+08:00",
  "timezone": "Asia/Shanghai",
  "time_role": "order_created",
  "granularity": "day"
}
```

此对象放入查询的 `time`，并选择定义了该时间字段、角色及粒度的指标。编译器使用已声明时区转换绑定参数，并以固定偏移和 DATE_FORMAT 生成分组，不依赖 MySQL 命名时区表。

DATETIME 范围内的存储时区偏移必须保持不变；按 datetime 字段分组时，查询时区的偏移也须保持不变。跨夏令时变化、重复的当地时间边界或历史秒级偏移返回 `UNSUPPORTED_TIMEZONE_TRANSITION`，需拆分为可验证区间；不能隐式近似。UTC TIMESTAMP 的范围筛选可跨夏令时变化，跨变化的当地时间分组仍不支持。上述 MySQL datetime 时间转换区间最多 100 年，超出返回 `UNSUPPORTED_TIMEZONE_RANGE`。缺少存储时区返回 `DATETIME_TIMEZONE_REQUIRED`。

## 兼容范围与执行边界

| 项目 | MySQL 5.7 标准指标支持 |
| --- | --- |
| 来源 | 同一连接中的 InnoDB BASE TABLE；列、类型、完整唯一键、数据库方言与 Catalog 绑定均复核 |
| 聚合 | `sum`、`count`、`count_distinct`，各指标独立过滤与聚合 |
| 多指标对齐 | 派生表、UNION 分组键和 NULL 安全关联，保留只有某个指标命中的分组 |
| 关联 | 经物理唯一键证明的 many-to-one / one-to-one；关系键物理 COLUMN_TYPE、字符集、排序规则及 datetime_timezone 必须兼容，避免隐式转换放大行数；LEFT 目标过滤保留在 ON |
| 表达式 | 受限四则运算；依赖指标先聚合；含除法的指标仅支持零分母返回 NULL |
| 方言约束 | 不生成 CTE、窗口函数、FILTER、FULL OUTER JOIN 或 PostgreSQL 类型转换语法 |
| 精度 | DECIMAL 和超出 JavaScript 安全范围的整数传为精确字符串；不会转成浮点图表数据 |
| 来源变化 | 重读物理结构和认证指纹，失配拒绝；不能靠修改模型标签绕过物理校验 |
| 不支持 | MyISAM、视图、跨事实联合计算、跨连接查询、任意 SQL 表达式、自动预聚合改写、未定义汇率的跨币种汇总 |

编译器使用参数绑定和标识符引用；执行由服务端重新编译，不接受客户端自制 SQL。标准执行使用同一连接的 REPEATABLE READ 一致性快照和 READ ONLY 事务完成结构核对与数据读取，结束后回滚。服务设置严格 SQL 模式、UTC 会话时区、确定的除法精度，以及查询、元数据锁等待和网络超时。Chat 取消通过同账号的控制连接关闭本次查询连接，无需 SUPER 或 PROCESS；控制连接不可达时仍保留服务器超时上限。

读取结果时遇到旧表中的零日期（如 `0000-00-00`）或不能转换的 DATE / DATETIME / TIMESTAMP，标准查询会报错，不把它们悄悄变成 SQL NULL。真正的 SQL NULL 仍保持为空；这项检查不代表已扫描并清洗业务库的全部历史数据。

`column_metadata` 包含 `name`、`dialect="mysql"`、`mysql_type_code`、`postgres_type_oid=null` 和 `json_encoding`。空表也保留列信息。前端展示驱动类型码和编码方式；旧 PostgreSQL 历史只有类型 OID 时继续兼容。金额、高精度过滤和表达式常量可使用十进制字符串，客户端不要将它们再次转换为 `Number`。

本次目标为 Oracle MySQL 5.7，运行时最低要求 5.7.8，测试实例版本为 5.7.44。MySQL 8、MariaDB 或其他兼容协议服务不在本次验收声明内；连接器能够建立连接不等于通过标准指标认证。

## 可复现验证

使用隔离 MySQL 5.7 测试库和已迁移的隔离 PostgreSQL 元数据库。测试会创建、修改、删除随机前缀表和目录记录；不得对生产库运行。MySQL 准备两个不同账号：fixture 管理账号用于该测试库的建表/改表/删表及数据准备，reader 账号只拥有该库 SELECT 权限。确认 `SELECT VERSION()` 是 `5.7.*`，并使 fixture 管理连接和应用运行时使用一致的 UTF-8 数据。

设置以下环境变量，替换成自己的测试连接串（不要提交真实密码）：

```bash
export AUTO_ONTOLOGY_MYSQL57_TEST_DSN='mysql://reader:REPLACE@127.0.0.1:3307/ontology_test'
export AUTO_ONTOLOGY_MYSQL57_ADMIN_DSN='mysql://fixture_admin:REPLACE@127.0.0.1:3307/ontology_test'
export AUTO_ONTOLOGY_GOVERNED_TEST_DSN='postgresql://test_user:REPLACE@127.0.0.1:5432/ontology_test'
uv sync --locked --group dev
uv run --no-sync pytest auto_ontology/connectors/tests/test_mysql57_read_transaction.py auto_ontology/connectors/tests/test_mysql_read_transaction.py auto_ontology/semantic/contracts/tests auto_ontology/server/metrics/tests -q -rs
```

`POSTGRES_*` 应指向上述同一个隔离 PostgreSQL 元数据库，并按现有指南先执行 Alembic 和 Prisma 同步；仅业务 MySQL 不运行这些迁移。集成测试缺少 DSN 时会跳过，因此不能把退出码 0 当成已完成 MySQL 联调；检查 pytest 的 passed/skipped 摘要。仓库 Python CI 已配置 MySQL 5.7.44 服务、SELECT-only 账号与两个元数据库 schema。需要单独确认真实 MySQL 生命周期、时间和 Chat 时可运行：

```bash
uv run --no-sync pytest auto_ontology/server/metrics/tests/test_mysql57_integration.py -q -rs
cd frontend
npx --yes pnpm@10.34.3 install --frozen-lockfile
npx --yes pnpm@10.34.3 exec vitest run
npx --yes pnpm@10.34.3 exec tsc --noEmit
npx --yes pnpm@10.34.3 lint
npx --yes pnpm@10.34.3 build
```

验证记录（本次开发）：

- 真实 Oracle MySQL Community 5.7.44 环境已验证只读账号、utf8mb4、精确 Decimal、UTC 会话、服务器超时及取消自己的查询。
- [MySQL 5.7 集成测试](../auto_ontology/server/metrics/tests/test_mysql57_integration.py) 已通过：真实 SELECT 账号、PostgreSQL Catalog/Prisma、FastAPI TestClient 与真实角色查询，覆盖创建→校验→认证→激活→编译→执行、精度、NULL/空表、多指标/复合指标、时间字段、来源漂移和权限隔离。Chat 使用真实 metrics_flow 与 PostgreSQL 历史持久化，模型解释候选由显式 stub 提供。
- 前端 37 项测试、TypeScript 检查、Prettier、ESLint（零错误）和 Next.js 生产构建通过。ESLint 保留既有 UserMenu 导航警告；未配置默认认证数据库的构建环境仍有既有 OAuthResource 连接和 Better Auth 环境提示，不等于已完成登录环境验收。
- 全量后端回归及最新结果由 [实施记录](plans/governed-metrics-progress.md) 统一记录；本指南不把单项测试通过当作全量无差异。

没有生产数据库或真实 LLM 密钥；自动化使用的 scripted 模型替身只验证系统流程，不证明自然语言理解质量、浏览器端到端交互或生产业务结果正确。

## MySQL 官方语义参考

- [MySQL 5.7 时区支持](https://dev.mysql.com/doc/refman/5.7/en/time-zone-support.html)：DATETIME 与 TIMESTAMP 的区别、会话时区。
- [MySQL 5.7.8 变更](https://dev.mysql.com/doc/relnotes/mysql/5.7/en/news-5-7-8.html)：`max_execution_time` 与查询超时语法。
- [InnoDB 只读事务](https://dev.mysql.com/doc/refman/5.7/en/innodb-performance-ro-txn.html)：只读事务语义。应用同时限制来源与 SQL，不能只靠事务标签替代权限和校验。
