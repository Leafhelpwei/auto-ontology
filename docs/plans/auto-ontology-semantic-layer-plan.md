<!--
SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# Auto Ontology 语义层增强：规划与执行任务书

- 文档版本：1.0
- 日期：2026-11-11
- 执行对象：承担仓库开发、测试和交付的 coding agent
- 主体项目：[NVIDIA/auto-ontology](https://github.com/NVIDIA/auto-ontology)
- 参考项目：[zao-sheng/Ontology-Playground-zh-dev](https://github.com/zao-sheng/Ontology-Playground-zh-dev)
- 执行顺序：P0 口径保留与结构化契约 → P1 统一校验与确定性查询 → P2 业务编辑与解释界面。
- 文档状态：规划基线，运行默认值已按 2026-11-13 的集成要求更新为开启。首版实现范围、已执行测试和剩余工作见 [实施记录](governed-metrics-progress.md)；本文件的目标不等于全部功能已完成或企业口径已认证。

## 1. 任务目标与实施原则

在 Auto Ontology 现有的数据库接入、Catalog、Term、ColumnAttribute、SqlAttribute、认证、问答和执行体系上，增加可版本化的指标与关系契约，以及受这些契约约束的标准指标查询路径。

最终应能完成以下闭环：

1. 从真实数据结构和历史查询产生有来源的语义候选。
2. 业务人员补齐并确认指标范围、公式、维度、时间、单位和关联规则。
3. 系统将完整定义保存为有版本的契约，并记录审核证据。
4. 用户问题被转换为结构化请求；后端统一校验后生成 SQL。
5. 查询复用现有数据库执行与权限体系，返回结果和实际使用的口径。
6. 语义定义变化后，相关认证、缓存和依赖得到处理，不继续使用过期口径。

实施遵循以下原则：

- 保留 Auto Ontology 主体架构，采用增量模块、增量迁移和受控查询入口。
- Playground 用于借鉴模型与交互；不整体移植其 OAG 编译器、规则匹配器或样例映射。
- 所有可执行字段必须经过服务端校验。前端校验、提示词和自然语言说明不能替代后端规则。
- 所有指标必须能追溯到实际 Catalog ID、字段、来源查询或已审核定义。
- 自动生成的是候选；静态检查和自动测试通过，不自动代表真实企业口径已获业务认可。
- 标准指标路径不支持的请求，返回结构化原因或澄清项，不静默改写为另一个问题。
- 实施过程中保留现有功能和未提交的用户改动；在目标开发仓库/分支工作。生产发布、生产数据修改和真实业务认证按实际授权处理。

## 2. 调研基线与开工核对

### 2.1 固定源码基线

| 项目 | 本文核对的提交 | 用途 |
| --- | --- | --- |
| Auto Ontology | `fc43a82600e18b19d159c52023f54f5572f537ac` | 当前架构、SQL 提炼与治理能力依据 |
| Playground 中文二开 | `a7dc01dc03ad74277de83c7bf99a0655c82d2b4b` | Metric、数据源映射、MQL 和编辑交互参考 |

这些提交用于定位证据，不要求将现有开发仓库回退到该版本。开工时记录实际 HEAD，并重新核对相关函数和迁移机制；已修复的问题改为补回归验证，避免重复实现。

### 2.2 已确认的事实

| 事实 | 实施含义 |
| --- | --- |
| Auto 已有真实数据库查询、SqlAttribute、认证、物理映射和可视化 | 扩展现有能力，不另造整套平台 |
| Auto 的逐表生成路径不等于底层模型只能一表一概念 | 业务对象合并需要明确身份与来源，不按同名自动合并 |
| 部分历史 SQL 表达式包装路径只保留 FROM 和部分 JOIN，未完整保留 WHERE/GROUP BY/HAVING | P0 优先保护完整口径和查询依赖 |
| Auto 建模指南已要求粒度、单位、过滤、可加性、时间及反例验证 | 本次将要求落成数据结构、校验及运行时行为 |
| Playground 有独立 Metric、多个来源映射、指标族和 MQL | 可借鉴数据表达和交互 |
| Playground 当前选源、复合键、时间、多个指标过滤等实现存在缺口 | 不将其现有翻译结果作为正确性基准 |
| 两者模型交换格式不同 | 不假定 RDF 或 Ossie 转换可以无损携带新增契约 |

### 2.3 Agent 开工清单（S0）

- [ ] 确认目标仓库、工作分支、实际 HEAD、工作区状态和已有改动。
- [ ] 阅读根目录 `AGENTS.md`、`CLAUDE.md`，以及修改目录下的相关规范；前端还须读取 `frontend/AGENTS.md`、`frontend/CLAUDE.md`。
- [ ] 读取现有数据库模型、迁移、SQLAttribute、认证、权限、查询与 OpenAPI 生成路径。
- [ ] 核对基线与目标版本的差异，列出复用点和已修复点。
- [ ] 运行与计划修改范围对应的基线测试，记录环境及已有失败。
- [ ] 建立进度文件，记录每个任务的状态、实现提交、测试证据和未解决项。
- [ ] 在隔离测试数据库创建第 9 节夹具，真实数据库结果作为验收依据。
- [ ] 将接口和契约的首版决策写入 ADR；只对影响业务口径的未决问题请求业务确认，常规实现选择由 agent 自行落实。

S0 产物：基线记录、ADR、任务清单、可运行夹具。完成后直接进入 P0，不停留在规划阶段。

## 3. 首版范围与明确边界

### 3.1 首版必须实现

- PostgreSQL 上的标准指标查询；其他已有数据库连接和旧查询能力保持兼容。
- 原子指标与同一基础事实来源上的复合指标。
- 单一事实行粒度，以及经认证的 many-to-one / one-to-one 维度连接。
- 完整复合 JOIN 条件、业务角色、NULL 行为和连接基数约束。
- 多个指标各自的过滤范围；同一请求中不同口径相互隔离。
- 指标维度/时间粒度白名单、单位/币种约束、空集合和零分母行为。
- 明确的时间字段、时区、范围和分组规则。
- 确定性选源：先检查能力与口径，再按已配置优先级选择；保留选择理由。
- 指标版本、依赖版本、认证证据、语义变更失效、并发更新保护。
- 统一后端校验，API、Chat 接入和界面使用相同规则。
- 口径澄清、编辑、验证和查询依据展示。

### 3.2 首版不支持，但必须可识别并拒绝

| 场景 | 首版行为 | 后续扩展条件 |
| --- | --- | --- |
| 不同事实身份或不兼容事实粒度的联合指标计算 | 返回 `UNSUPPORTED_MULTI_FACT`，不得直接大 JOIN | 分别聚合后的对齐规则、公共维度、范围及缺失值语义已定义 |
| 未配置分摊规则的 many-to-many，或会扩大事实行数的连接 | 返回 `FANOUT_RISK` | 有经验证的桥表/分摊/去重方案 |
| 自动使用日/月预聚合表替换明细 | 首版选源排除预聚合候选；记录原因 | 聚合状态、可再聚合性和等价性测试完善 |
| 非 PostgreSQL 的新增标准指标路径 | 返回 `UNSUPPORTED_DIALECT` | 该方言的编译与结果测试通过 |
| 窗口、复杂嵌套、相关子查询、集合运算等超出首版表达式子集 | 历史 SQL 作为完整候选保存；新编译路径明确拒绝 | 明确增加对应 IR 能力与结果级测试 |
| 缺少历史事实却请求历史状态 | 返回 `UNSUPPORTED_HISTORICAL_STATE` | 有时态数据及明确的时点口径 |
| 无汇率定义却要求跨币种总额 | 返回 `UNIT_MISMATCH` | 有汇率来源、换算时间和舍入规则 |

首版“可支持”的范围由实现与测试共同证明，不因字段已存在就宣称能力已完成。拒绝不等于整项任务结束；其余已支持的任务继续完成。

多事实判定依据事实身份、行粒度和来源绑定，不依据治理责任人。`owner_term_id` 表示业务归属，`data_owner`/账户 owner 表示治理责任，不能混用；同一事实的多个替代来源也不自动构成多事实。

## 4. 目标模块与兼容方式

### 4.1 复用与新增

| 层次 | 复用 Auto 能力 | 本次新增 |
| --- | --- | --- |
| 数据接入 | 连接器、Catalog、采样、主外键、数据源访问上下文 | 来源结构指纹及绑定完整性检查 |
| 语义发现 | Term/ColumnAttribute、SQLAttribute 提炼、来源关联 | 完整 SQL 上下文证据、指标候选分类 |
| 语义治理 | 现有 owner、认证身份、修改记录与管理界面 | MetricRevision、RelationshipRevision、SourceBinding、依赖及审核记录 |
| 查询理解 | 现有意图识别、语义检索和澄清机制 | 输出结构化 MetricQueryRequest |
| 查询规划 | 现有物理映射、连接器方言信息 | 统一 validator、类型化计划、选源及聚合规则 |
| 执行返回 | 现有 SQL 执行、超时、用户身份与结果处理 | 计划绑定、契约版本和实际 SQL 依据 |
| 管理交互 | 现有 Next.js 页面、表单和权限 | 指标编辑、来源/关系编辑、验证报告、口径选择 |

不得把 fact/dim 固化为整个本体的唯一概念分类。它们是查询中的分析角色；Term 仍可表达更广泛的业务对象。

### 4.2 兼容要求

- 新表、新字段和新接口采用增量方式；现有 Term、ColumnAttribute、SqlAttribute ID 不变。
- 新指标通过可选引用关联 SqlAttribute；筛选集合、连接定义等 SQLAttribute 不强制转为 Metric。
- 旧 `certified` 标记不能直接认证一个新建的指标契约版本。
- 现有探索性查询入口保持可用；受治理运行时默认开启，可显式关闭，标准查询仍须使用已认证、已激活的契约。
- 新路径已经命中指标但校验失败时，不自动转入旧路径绕过错误。
- 关闭功能开关只改变路由，不删除新契约；结果需区分所走路径，不能把旧路径标为“已按契约执行”。
- 新元数据必须纳入备份、原生导入导出或独立契约包。暂不支持的外部格式转换必须列出丢失字段并拒绝无提示的覆盖导入。

## 5. 数据契约与状态设计

以下为本项目拟新增的设计要求，不是声称 Auto 当前已有这些字段或 API。具体物理表名和路由由 S0 确认，保持仓库命名习惯。

### 5.1 指标契约 MetricRevision

稳定的 `metric_id` 表示一个业务指标；不可变的 `revision_id` 表示其具体定义。建议使用关系表管理身份、版本和依赖，复杂表达式使用经 DTO 校验的 JSON/AST；不把整份无约束 JSON 当作唯一模型。

| 字段组 | 必须表达的内容 |
| --- | --- |
| 身份 | metric_id、revision_id、技术名、中文展示名、业务说明、owner_term_id、业务域 |
| 类型与依赖 | atomic/composite；固定到 revision 的子指标依赖；无环检查 |
| 公式 | 类型化表达式；仅引用已登记的字段/指标 ID；声明计算结果类型 |
| 记录范围 | 基础事实来源、完整行粒度、必带谓词、业务排除范围 |
| 维度与粒度 | 允许维度、允许时间粒度、各维度对应的授权关系路径 |
| 时间 | 时间角色、物理字段引用、字段类型、时区、范围语义、有效版本 |
| 单位 | 物理单位；货币型指标的币种字段或固定币种；精度与舍入 |
| 聚合 | sum/count/count_distinct 等首版允许类型、可加维度、禁止重聚合规则 |
| 空值与边界 | 输入 NULL、空集合、缺失分组、零分母的明确行为 |
| 指标族 | family_id；族内默认口径；默认口径的作用域及唯一性 |
| 来源证据 | catalog/source IDs、来源 SQL、方言、结构指纹、人工依据及来源版本 |
| 治理 | 状态、审核者、审核时间、认证依据、有效期、修改者、并发版本 |

`do_not` 可用于展示业务边界，但可执行的排除条件必须另存为结构化谓词。文本写了“不含取消订单”，却没有对应约束时，禁止进入可发布状态。

### 5.2 关系契约 RelationshipRevision

至少包含：

- 稳定关系 ID、版本、源/目标 Term、业务角色、方向。
- 源/目标 Catalog 字段引用列表；复合键必须完整配对。
- 关系基数、源列可空性、目标唯一范围、允许查询方向。
- INNER/LEFT 的业务语义、无匹配记录如何处理。
- 租户、来源系统、版本、有效期等完整适用条件。
- 观察到的连接覆盖、唯一性/扩行验证结果、检查的数据版本及时间。
- 已审核的路径、证据与状态。

已声明外键与数据采样只能提供证据，不自动认证业务角色和完整基数。优先复用数据库唯一约束；只有采样证据时必须标明其不足，不伪装成全量证明。

### 5.3 来源绑定 SourceBinding

将业务对象/属性绑定到具体的数据库、schema、表和列 ID，并明确：

- 行粒度、可用属性、维度和时间字段。
- 必带来源过滤、授权范围、来源状态和版本。
- 明细/预聚合标志、权威级别、明确的选择优先级。
- 可支持的指标版本或能力签名。
- 结构指纹与依赖状态。

选源顺序固定为：访问范围检查 → 状态/版本检查 → 字段与关系可用性 → 粒度、单位和范围兼容性 → 已配置优先级。无有效来源则拒绝；多个同优先级来源有语义差异时返回歧义。不能依据“映射列最多”或 `preAggregated=true` 自动选择。

### 5.4 认证与变更

建议生命周期：`draft → validated → certified → deprecated`。

- `validated` 表示结构和约定测试通过；`certified` 还要求有权限的业务审核证据。
- 每次认证绑定具体 revision、依赖版本、来源结构指纹与检查报告。
- 修改公式、过滤、粒度、关系、单位、时间或来源，产生新草稿版本，不继承旧认证。
- 已绑定的语义依赖发生失效事件时，受影响版本标记待复核；结构不兼容时禁止继续执行。
- 新草稿不覆盖仍有效的旧版本；只有明确激活后才更换默认指向。
- 纯展示文案修改是否影响认证必须分类处理；含业务含义的描述变化需要复核。
- 普通数据新增不等同于结构变更，不应无条件使所有认证失效；约束验证失败按质量事件处理。
- 生命周期与可执行性分开记录：另设 valid/needs_review/blocked 及原因。依赖锁定到明确 revision；仅新增依赖草稿或发布不相关新版本，不使仍有效的旧绑定失效。只有已绑定依赖撤销、来源不兼容或明确质量失败才按影响范围阻止执行。
- 并发修改采用 expected_version/ETag 等方式拒绝覆盖，不执行最后写入者静默获胜。
- 复用现有权限检查与审核身份；机器测试账号的演示认证只作用于测试夹具。
- 草稿允许经 catalog 编辑权限检查的专用验证/预览操作，显式标记未认证，并绑定该草稿版本；普通标准指标查询入口仍拒绝草稿，避免预览能力绕过发布状态。

## 6. 分阶段执行任务

### P0：修复口径保留，落地结构化契约

#### P0-1 历史 SQL 上下文保留

**代码切入：** `auto_ontology/semantic/sql_attribute_suggester.py`，尤其表达式抽取、`_try_wrap_in_select`、`_build_valid_sql`；SQLAttribute 服务、DAL 与来源关联。

**实施：**

1. 先为已知失败场景建立回归测试，再修改实现。
2. 每个候选保留完整原 SQL、数据库方言、来源 ID、被提取表达式、作用域及上下文证据；限制重复存储并保护已有访问范围。
3. 用 SQL AST 追踪 FROM、JOIN、WHERE、GROUP BY、HAVING、DISTINCT、CTE、别名和依赖闭包；候选去重采用完整规范化口径指纹，同一 SUM 表达式在不同过滤、粒度或单位上下文中不得误合并。
4. 首版对复杂查询优先保留完整 SQL 作为 opaque candidate。无法证明可独立提取时标记不可编译及原因，不生成含义变化的可复用指标。
5. 不机械拼回原 WHERE：需判断该条件属于记录范围、临时查询参数、分组后过滤还是来源访问约束。
6. 把“完整查询可复用”与“可抽取指标模板”分开；临时日期/ID 条件未经确认不能提升为企业默认口径。
7. 候选的字段、连接依赖必须能在 Catalog 解析；完整链式 JOIN 不得只留下末端表。
8. 对支持的抽取形式，以独立基准 SQL 比较结果；含 DISTINCT、NULL、重复值和空集合的反例必须覆盖。

**验收：**

- 已确认金额候选不会丢失 `status_code='CONFIRMED'` 或币种分组。
- `a → b → c` 链式 JOIN 的中间依赖不会丢失。
- 不支持的窗口/相关子查询等保留原文并拒绝模板化，不伪造成功。
- 来源关联、认证记录和已有调用接口保持兼容；修复错误去重语义，不以兼容为由保留口径误合并。
- 未经人工批准，不批量重写或重新认证现有生产 SQLAttribute。

#### P0-2 契约模型、持久化与 API

**代码切入：** `auto_ontology/dal/schema.py`、`alembic/`、SQLAttribute DAL/服务、现有 API 注册及前端代理。

**实施：**

1. 新增第 5 节模型的 DTO、持久化、迁移、查询与变更服务。
2. 将 Catalog/Term/ColumnAttribute/SqlAttribute 引用做成可验证关系，保持原 ID。
3. 为表达式、过滤和依赖定义受限的类型化表示；决定首版支持的运算符、函数及数据类型。
4. 提供草稿创建/更新、读取、验证、认证/激活、停用及版本查询操作；这是待实现接口，不能用猜测的现有 endpoint 代替。
5. 契约修改复用真实身份与权限；所有执行请求也必须校验数据源范围。
6. 同提交补齐 API 规范、迁移 head 配置、备份导出/恢复路径和幂等导入测试。
7. 读取 `alembic/env.py` 和实际 schema 配置确定归属，不依赖过时注释；遵循 SQLAlchemy Core/Alembic 现有方式。
8. 新增 migration 时同步部署所用的 Alembic revision pin，并运行对应检查；不得修改已应用 migration。
9. 契约更新、版本检查、认证变化及依赖状态调整在同一事务边界完成；embedding 等异步工作明确记录待处理状态，失败不能造成新定义沿用旧认证。

**验收：**

- 新旧模型并存；旧数据读取、旧查询和旧模型导出有明确兼容行为。
- 不能保存指向不存在/无权限对象的契约，也不能自行标成已认证。
- 迁移升级和回滚策略在隔离库验证；生产回退优先关闭新路径，不直接删除元数据。
- 受控的可信服务端备份恢复后，稳定 ID、版本、依赖、规则及历史认证证据保持一致；重新检查身份、来源和有效性后才恢复可执行状态。不支持的转换明确报错。
- 普通上传/外部导入只保留历史认证信息作为来源证据，重新绑定实际 Catalog，默认生成草稿/待复核，不能通过上传 certified 字段自行认证或自动激活。

#### P0-3 变更影响与发布条件

**实施：**

1. 建立 metric → dependency revision → source/relationship revision 的依赖索引。
2. 实现循环依赖、默认口径冲突和失效依赖检查。
3. 将审核绑定 revision；公式、过滤等语义变更不能保留旧认证作为新版本的认证。
4. 对已安装版本的 SQLAttribute 更新逻辑做专项核查，覆盖“改 expression 后仍保留 certified”的风险。
5. 管理操作返回变更摘要、影响指标、需要重测的用例及可回退版本。
6. 测试夹具可由专用 fixture 自动注册；真实业务定义缺失时保持草稿，继续完成可独立推进的开发。

**P0 阶段门槛：** 上下文保留回归通过；契约可存取、可验证、可追溯；迁移及兼容测试通过；语义变化认证不会误继承。

### P1：统一校验与确定性查询

#### P1-1 结构化请求与统一 validator

拟新增的请求协议包含：

| 部分 | 内容 |
| --- | --- |
| 指标 | metric_id，或明确的族选择；执行前解析到 revision_id |
| 维度 | dimension_id 列表，必要时含关系角色/路径 |
| 时间 | time_role、start、end_exclusive、timezone、granularity |
| 过滤 | 已登记字段 ID、允许操作符、类型化值 |
| 输出 | order_by、limit；仅允许引用已选择的维度/指标 |
| 上下文 | 由服务端取得用户/权限/数据源范围；客户端不可伪造认证状态 |

**实施：**

- unknown metric/dimension/filter 必须报错；部分字段识别失败不得静默丢弃后继续执行。
- 统一检查版本状态、依赖、权限、维度/粒度、单位、时间、连接、选源和表达式类型。
- 缺少关键口径返回 `needs_clarification`；未支持组合返回 `unsupported`；契约无效返回 `invalid`。
- 时间不隐式使用近 30 天，也不默认选择第一个 date 字段。缺省时间只能来自经确认的契约，并在计划中展开为绝对边界。
- 时间范围采用半开区间 [start, end)，按明确时区转换；分组粒度也按该时区处理。
- 允许全量查询时必须显式表达 all_time；不能把未解析到的日期当作全量。
- 首版不支持聚合后指标过滤（如“金额大于 1000”），需明确返回不支持；不得忽略该条件。支持的排序/limit 在所有聚合与复合计算后生效，并定义稳定的并列排序规则。
- 指标族多个有效候选且无适用默认时，先澄清；选择结果只作用于当前请求。
- 增加单位检查：货币汇总按币种分组或限定为单一币种；不同币种不能直接相加。
- 校验必须位于后端共享服务，直调 API、Chat 和将来的 CLI 使用一致路径。

**验收：** 所有入口对相同无效请求返回同类错误；未认证或过期契约不能在标准路径执行；不因 LLM 修改 SQL 而绕过规则。

#### P1-2 计划 IR、SQL 编译与执行

**实施：**

1. 先生成类型化逻辑计划，再使用 AST/参数化机制生成 PostgreSQL SQL；禁止正则字符串替换作为核心编译方式。
2. 计划固定指标/关系/来源 revision、物理绑定、用户权限上下文、时间、过滤、JOIN 和聚合策略。
3. 同一原子指标组合来源条件、指标条件、合法用户条件；必带条件不能被覆盖或删除。
4. 不同指标分别应用各自范围，再在相同输出维度上对齐。可使用独立 CTE 后合并或等价条件聚合，但须证明不互相污染。
5. 合并多个指标时定义分组集合：首版取各指标分组键的并集；NULL 键采用一致且明确的匹配语义。不得 INNER JOIN 丢掉仅某个指标存在的分组。
6. 分组缺失后的填充值按契约执行。默认 SUM 无数据为 NULL、COUNT 为 0；固定维度补零需显式配置。
7. 复合指标在原子指标聚合后计算；比例按“总分子/总分母”及零分母规则执行，不平均各行比率。展开依赖后验证输出维度、时间角色/范围与单位运算兼容，保留各子指标过滤；未定义创建时间与确认时间的对齐规则时拒绝组合。
8. 同事实的原子指标使用同一获批基础来源；无法找到覆盖全部所需字段和口径的来源时拒绝。不同事实身份/粒度的组合首版拒绝，不因治理 owner 不同而拒绝。
9. JOIN 编译所有键对和附加条件；支持已声明的 INNER/LEFT 语义；遵守关系方向与基数。
10. 需要跨一对多路径扩大事实行数时拒绝，除非已有明确支持并验证的处理策略。不能使用 `SUM(DISTINCT amount)` 当作通用去重。
11. 首版选择明细来源；预聚合表记录为不支持候选。以后支持前须额外定义 AVG、DISTINCT COUNT 等可再聚合规则。
12. 标识符通过已验证的 Catalog 引用按方言引用；用户值参数化；过滤算子来自白名单。核对并补齐 planner → executor → connector → driver 的参数传递，不在执行前用字符串替换占位符冒充参数化。
13. 复用现有只读查询执行、数据源路由、超时和结果处理。编译 SQL 可再次检查，但修复逻辑不能改变契约。
14. 执行前重新检查版本与权限；预览计划和最终执行发生变化时要求重新规划，不能接受客户端篡改的 SQL。
15. 返回数据库结果、具体版本、实际 SQL/参数的授权可见表示、字段映射、JOIN 条件、过滤、时间、单位和选源理由。

**验收：**

- 所有支持场景与独立基准 SQL 的结果集一致，而非只验证出现了 SELECT 或某个字符串。
- 相同请求、相同契约与 Catalog 快照产生稳定计划；相对时间先解析再固定。
- 多指标不同过滤、复合键、空集合、NULL 分组、时间边界和重复金额反例通过。
- 未支持场景没有执行数据库 SQL；执行异常与业务不支持不能混为成功结果。

#### P1-3 接入现有问答链路

**代码切入：** `auto_ontology/retrieval/text_to_sql/` 的意图、候选检索、语义 SQL 与执行节点；现有 Chat API。

**当前集成实现（2026-11-13）：** 通过 `auto_ontology/server/chat/metrics_flow.py` 接入既有 Chat 路由、worker、SSE 与会话持久化；标准指标模式复用 `server/metrics/` 共享服务，探索模式保留原 Text-to-SQL 路径。模型选择、澄清续问及版本绑定由显式模式和服务端会话上下文承载。实际验证范围以 [实施记录](governed-metrics-progress.md) 为准，仍需独立完成真实 LLM 和生产业务验收。

**实施：**

- 检索到指标后，模型只提出结构化请求，后端解析到稳定 ID 和版本。
- 对标准指标请求走统一 validator/planner/executor；澄清结果可继续同一次会话。
- 对一般探索性查询保留原路径，明确其能力边界；不能将标准路径拒绝的请求自动转为探索性执行。
- 在响应中区分“按认证契约执行”“待澄清”“不支持”“探索查询”，避免标签混用。
- 端到端测试覆盖一句问题、澄清选择、计划、数据库结果和口径展示。
- 服务/API 核心验收使用确定性请求；在线模型调用作为独立评估，不成为单元测试隐式依赖。

**P1 阶段门槛：** 统一后端规则实际生效；标准指标问题完成真实 PostgreSQL 查询闭环；第 9 节适用验收全部通过。

### P2：业务编辑、口径选择与查询解释

#### P2-1 管理界面

基于现有 Next.js 管理界面扩展，不整体替换为 Playground 的 React/Vite 应用。

- 指标列表与详情：名称、类型、owner、族、版本、状态、依赖、来源。
- 编辑：公式、范围、过滤、维度、时间、单位、空值及零分母规则。
- 关系：业务角色、完整复合键、JOIN 类型、附加条件、基数和验证证据。
- 数据源：映射、适用能力、优先级、状态、结构变更与排除原因。
- 草稿保存、验证报告、前后差异、影响范围、认证/激活与停用。
- 物理 ID 通过 Catalog 选择器解析；显示业务名，但不依赖名称作为稳定身份。
- 前端校验改善体验，最终结果以共享后端规则为准。
- 操作成功回读已保存版本；并发冲突提示差异，不覆盖他人更新。
- 现有认证控件可能独立于文本 Save 立即保存；新界面只认证明确已保存的 revision，认证请求带 expected_revision，防止用户以为认证了草稿却实际认证旧内容。

#### P2-2 查询与解释界面

- 指标族候选卡显示定义、计算范围、时间口径和单位。
- 查询前可查看语义请求；用户修正后重新校验。
- 结果展示实际使用的指标版本、源表、关系、过滤、时间和单位。
- 区分真实执行结果、仅编译预览、示例数据；不展示模拟耗时为真实性能。
- 不支持时说明缺少哪项规则及可执行的补充方向。
- 避免在业务主流程暴露内部 AST 等实现细节；开发诊断另设入口。

**P2 阶段门槛：** 有相应权限的用户可通过界面完成草稿 → 验证 → 认证/激活 → 查询；修改口径后可看到版本和影响，旧认证不会误作用于新版本。

## 7. 代码定位与拟新增模块

### 7.1 已确认的现有切入点

| 位置 | 关注内容 |
| --- | --- |
| `auto_ontology/semantic/sql_attribute_suggester.py` | 提取、排序、包装 SQLAttribute 与来源关联 |
| `auto_ontology/server/sql_attributes/service.py` | 校验、创建、更新及认证兼容 |
| `auto_ontology/dal/sql_attributes.py` | SQLAttribute 持久化与引用 |
| `auto_ontology/dal/schema.py` | SQLAlchemy Core schema 单一事实来源 |
| `alembic/env.py`、`alembic/versions/` | Schema 归属、增量迁移和 head |
| `helm/auto-ontology/values.yaml` | 部署 migration revision pin |
| `auto_ontology/server/__main__.py` | FastAPI 应用注册；不要寻找不存在的 server/main.py |
| `auto_ontology/server/models.py`、`auto_ontology/server/responses.py` | DTO 与 response_model；避免新增字段在返回时被过滤 |
| `auto_ontology/retrieval/text_to_sql/agents/sql_from_semantic.py` | 现有 SQL 定义与 JOIN 上下文进入模型的位置 |
| `auto_ontology/retrieval/text_to_sql/text_to_sql_graph.py` | 插入标准指标查询分支的编排位置 |
| `auto_ontology/retrieval/text_to_sql/agents/sql_execution.py` | 真实连接器执行路径 |
| `auto_ontology/server/model_interchange/` | 原生/外部格式交换及保真限制 |
| `auto_ontology/semantic/tests/`、`auto_ontology/dal/tests/`、`auto_ontology/retrieval/text_to_sql/tests/` | 现有测试体系 |
| `frontend/`、`docs/openapi/` | 页面、公开 API 代理及接口规范 |

前端优先核对 `frontend/components/termsPage/TermsView.tsx`、`frontend/api/sqlAttributes.ts`、`frontend/types/terms.ts` 和现有认证组件。公开管理 API 继续通过 Next.js `/api` 及 `frontend/auth/with-auth.ts`、`proxy-backend.ts` 传递可信身份；新增 FastAPI router 不应变成浏览器直连入口。预览/编译同样检查字段与数据源可见范围。

### 7.2 拟新增模块（名称可按仓库习惯调整）

| 建议模块 | 职责 |
| --- | --- |
| `semantic/contracts/` | 契约 DTO、表达式类型、依赖与业务校验 |
| `dal/metric_contracts.py` | 版本与引用的持久化 |
| `server/metrics/` | 管理、验证、版本与查询 API |
| `retrieval/metric_query/` | 请求解析、计划、选源、SQL 编译与执行适配 |
| 管理界面的指标与契约组件 | 业务编辑、认证和差异展示 |
| 原生契约导出/恢复模块 | 保留新增版本、依赖与证据 |

以上均为建议新增路径；实施前检索现有同类模块，能复用则复用。不得维护两个独立的 validator，也不得为新功能创建独立身份系统。

## 8. 可并行分工与提交顺序

| 工作包 | 负责内容 | 依赖 |
| --- | --- | --- |
| A：SQL 保真 | P0-1、相关结果回归 | S0 |
| B：契约与治理 | P0-2/P0-3、迁移、API、导出 | S0，先冻结共享 DTO |
| C：独立验证 | 基准夹具、期望结果、边界与权限测试 | S0；避免复制编译器算法生成期望值 |
| D：查询编译 | P1-1/P1-2 | B 的契约稳定，A 的口径规则明确 |
| E：问答接入 | P1-3、澄清状态 | D 的请求/错误协议稳定 |
| F：业务界面 | P2 | B/E 的 API 稳定，可先做只读原型 |

共享 schema、migration head、DTO、OpenAPI 由明确负责人协调，避免并行产生冲突。每个工作包提交实现、必要测试、接口或运维文档及实际验证结果。

建议按 S0、P0-1、P0-2/P0-3、P1-1、P1-2、P1-3、P2 拆分可审查提交/PR。用户环境允许时在自有 fork/开发仓库交付；不默认向 NVIDIA 上游提交 PR。

## 9. 验收夹具与测试矩阵

### 9.1 基础夹具 F0：订单语义

沿用数据建模规范的业务边界：当前销售订单、按件销售；不含税、运费、退款、付款和发货事实。CONFIRMED 不等于 PAID。

基础表：

- `customer(customer_id PK, customer_code UNIQUE, customer_name)`
- `product(product_id PK, product_code UNIQUE, product_name)`
- `sales_order(order_id PK, order_no UNIQUE, customer_id FK, status_code, currency_code, created_at, confirmed_at)`
- `order_line(order_line_id PK, order_id FK, line_no, product_id FK, quantity, unit_price)`，唯一键为 `(order_id, line_no)`。

金额使用精确 DECIMAL；时间采用明确的带时区类型。客户 1/2/3 分别为 C001/C002/C003，商品 101/102 为 P001/P002。

| order_id | order_no | customer_id | 状态 | 币种 | created_at | confirmed_at |
| --- | --- | --- | --- | --- | --- | --- |
| 1001 | SO001 | 1 | CONFIRMED | CNY | 2026-11-01T08:00:00+08:00 | 2026-11-01T09:00:00+08:00 |
| 1002 | SO002 | 2 | CONFIRMED | USD | 2026-11-02T08:00:00+08:00 | 2026-11-02T09:00:00+08:00 |
| 1003 | SO003 | 1 | CANCELLED | CNY | 2026-11-03T08:00:00+08:00 | 2026-11-03T09:00:00+08:00 |
| 1004 | SO004 | 2 | DRAFT | CNY | 2026-11-04T08:00:00+08:00 | NULL |

| order_line_id | order_id | line_no | product_id | quantity | unit_price |
| --- | --- | --- | --- | --- | --- |
| 1 | 1001 | 1 | 101 | 2 | 100.00 |
| 2 | 1001 | 2 | 102 | 1 | 50.00 |
| 3 | 1002 | 1 | 101 | 1 | 100.00 |
| 4 | 1003 | 1 | 102 | 1 | 50.00 |

业务基准：

- 当前已确认订单 2 笔；与明细连接后为 3 行。
- 当前已确认商品金额 CNY 250.00、USD 100.00；不能合为“350 元”。
- 已取消订单商品金额 CNY 50.00，不能因 confirmed_at 非空计入已确认金额。
- 无任何订单的客户只有 customer_id=3。
- 不具备计算已付款金额的事实依据。
- 本夹具采用“已确认订单必须有明细”的质量规则；违反时报告质量问题，不通过内连接隐藏异常。

独立基准 SQL：

```sql
-- 订单数量：订单粒度。
SELECT COUNT(*) AS confirmed_order_count
FROM sales_order
WHERE status_code = 'CONFIRMED';

-- 商品金额：明细粒度，按币种输出。
SELECT o.currency_code, SUM(l.quantity * l.unit_price) AS confirmed_goods_amount
FROM sales_order o
JOIN order_line l ON l.order_id = o.order_id
WHERE o.status_code = 'CONFIRMED'
GROUP BY o.currency_code
ORDER BY o.currency_code;

-- 无订单客户。
SELECT c.customer_id
FROM customer c
WHERE NOT EXISTS (
    SELECT 1 FROM sales_order o WHERE o.customer_id = c.customer_id
)
ORDER BY c.customer_id;
```

前两条用于标准指标结果验收；第三条用于现有查询/关系兼容性验收，不要求首版指标 DSL 因此支持任意反连接。

### 9.2 独立扩展夹具

扩展夹具放在独立测试 schema 或每次回滚的事务中，不改变 F0 的固定期望。

| 夹具 | 最小输入 | 独立期望 |
| --- | --- | --- |
| F1：租户复合键 | A/B 都有 order_id=1；各一条明细，金额 10/20；关系键为 tenant_id+order_id | 分租户得到 A=10、B=20，连接行数 2；漏 tenant_id 的计划不允许执行 |
| F2：时间边界 | CNY 已确认记录，均按 confirmed_at：10@2026-10-31T23:59:59+08:00、20@11-01T00:00:00+08:00、30@11-30T23:59:59+08:00、40@12-01T00:00:00+08:00；将 20 的 created_at 设为 10 月 | Asia/Shanghai 的 11 月 [11-01,12-01) 为 50；UTC 边界为 10-31T16:00:00Z 至 11-30T16:00:00Z；偷用创建时间必须被测试发现 |
| F3：同商品多明细 | 同订单、同商品、两行不同 line_no，各金额 10 | 金额 20，明细数 2，订单数 1；不能按 order_id+product_id 去重 |
| F4：等额订单扩行 | 两笔不同订单，整单金额均 100；第一笔两条明细、第二笔一条 | 直接按订单来源汇总为 200；强制经过扩行路径时首版返回 FANOUT_RISK。JOIN 后求和 300、SUM(DISTINCT) 得 100 均为错误，不要求首版自动修复该路径 |
| F5：缺失/空集合 | 合法可空维度关联；无匹配范围；无明细的已确认订单单独作为坏数据 | LEFT 语义保留 NULL 分组；空集合按契约；坏数据触发质量失败 |
| F6：多事实 | 独立 payment 事实一行，支付记录与订单具有不同事实身份/粒度 | 联合标准指标请求返回 UNSUPPORTED_MULTI_FACT，不生成大 JOIN |
| F7：来源适用性 | 两个真实明细来源，一个缺客户维度；另有日汇总视图 | 按客户查询只能选择兼容来源；首版排除预聚合视图 |

F6 仅用于测试能力边界，不把 F0 的确认状态解释成支付事实。

### 9.3 必须通过的验收矩阵

| ID | 场景 | 通过标准 |
| --- | --- | --- |
| T01 | 历史 SQL 含状态过滤、币种分组 | 支持的提炼结果保持 F0 的 CNY250/USD100；不支持时保留完整查询并说明 |
| T02 | 链式 JOIN、别名、CTE、HAVING、DISTINCT | 支持项依赖完整、结果等价；不支持项明确拒绝模板化 |
| T03 | 取消但确认时间非空 | 已确认金额不包含 SO003 |
| T04 | 订单连接明细 | 订单数 2，连接行数 3；不得混淆 |
| T05 | 同时查询已确认/已取消商品金额 | 同一 order_line 基础来源；CNY=250/50，USD=100/NULL；无 USD 取消分组按 SUM 空集合规则 |
| T06 | 复合指标 | 同来源分子/分母分别聚合后计算；零分母按契约；不得平均逐行比例 |
| T07 | 币种 | 按币种结果正确；未限定币种且不分组的金额总和拒绝 |
| T08 | 指定时间范围 | F2 得 50；不受系统当前日期影响；说明与 SQL 边界一致 |
| T09 | 时间缺失/不适用 | 澄清或显式不支持，不默认近 30 天或错误时间字段 |
| T10 | 复合键与角色 | F1 正确；购买方/收货方有不同路径时按明确角色解析 |
| T11 | 明细身份与扩行 | F3 正确；F4 直接订单汇总为 200，强制扩行请求返回 FANOUT_RISK |
| T12 | NULL/空集合/外连接 | F5 按契约；NULL 分组不因多指标合并而消失 |
| T13 | 数据源选择 | F7 兼容性优先；没有兼容来源不执行 |
| T14 | 多事实 | F6 明确拒绝；普通单事实请求继续可用 |
| T15 | 白名单及请求完整性 | API/Chat/界面一致拒绝未知字段与未支持的指标过滤；排序、并列规则和 limit 在最终聚合后正确生效 |
| T16 | 必带过滤 | 指标及来源过滤实际进入 SQL，用户过滤不能取消它们 |
| T17 | 值和标识符 | 参数化值、正确引用标识符；无多语句执行或任意 SQL 拼入 |
| T18 | 状态与依赖 | 草稿/失效版本不可标准执行；依赖循环、过期和默认冲突被识别 |
| T19 | 认证与并发 | 语义变更新版本未认证；旧有效版本可追溯；并发修改返回冲突 |
| T20 | 权限与计划绑定 | 无权数据源不可预览/执行；篡改或过期计划不能执行 |
| T21 | 导入导出 | 可信备份恢复保留 ID/版本/依赖/历史证据并重新验证可执行性；普通导入为草稿，不自动认证；字段不静默丢失 |
| T22 | 旧路径兼容 | 原有代表性查询、SQLAttribute CRUD、权限和结果展示无回归 |
| T23 | 前端闭环 | 草稿编辑、错误定位、审核、澄清、实际结果和依据展示可验证 |
| T24 | 不可回答问题 | F0 上“已付款金额”返回缺少支付事实，不用已确认金额替代 |

为 T06 增加专门同来源 fixture：两个组分别为分子/分母 1/2 与 9/10，跨组总比率应为 10/12，不能取 (1/2+9/10)/2；再覆盖分母为 0 的情形。

测试契约固定为 DECIMAL 运算、比率输出 6 位小数（10/12 得 0.833333）、zero_division=NULL、SUM 空集合=NULL、COUNT 空集合=0。分别测试无分组空集合和有分组但某指标缺失；另测试同源但时间角色不兼容、金额相加币种不兼容时拒绝，金额除以订单数按派生单位校验。

为 T01 增加“相同公式、不同过滤”的去重反例：两条来源 SQL 仅 CONFIRMED/CANCELLED 不同，输出别名相同；必须分别保留证据与口径差异，不能按同名或裸 SUM 表达式合并。接入来源为 `pg_stat_activity` 时明确标记活动查询采样，不标为完整历史库。

测试期望由业务规则和独立基准 SQL 给出。编译器自生成 SQL 再自生成期望值，不构成独立验证。

## 10. 检查命令与证据要求

以目标版本的 `CLAUDE.md`、锁文件和 CI 为准；以下为已核对基线中的检查入口。

后端（仓库根目录）：

```bash
uv run ruff check auto_ontology/
uv run ruff format --check auto_ontology/
uv run alembic upgrade head
uv run alembic check
uv run pytest auto_ontology dev_tools -q
uv run python -m dev_tools.generate_backend_openapi
```

前端（在 frontend 目录）：

```bash
npx prettier --check .
pnpm lint
pnpm build
pnpm openapi
```

执行要求：

- 开发时先运行修改范围内的测试；阶段交付时运行相应全量检查及新增 PostgreSQL 结果测试。
- 新增或修改路由的签名、DTO、docstring 后，更新并提交受影响的 OpenAPI 文件；同时核对公开代理规范。
- 后端规范生成后，再在 frontend 运行 `pnpm openapi`，检查 `docs/openapi/` 与 MCP 打包规范的预期变化。如修改 alembic/dev_tools，同时对这些目录运行 Ruff。
- 迁移检查覆盖单一 Alembic head、部署 revision pin、升级及回退方案。
- 上述 Alembic 命令只用于已配置的隔离开发/测试库。基线中 SQLAlchemy/Alembic 管理 public，Prisma 管理 frontend，向量使用 vdb；以目标版本实际配置为准。
- 前端除构建外，验证关键界面流程；使用仓库已有 UI 测试工具，必要时做可复现的浏览器检查。
- 核对基线没有前端 `test` script；如新增交互测试，须同时交付运行命令和 CI 接入，不填写不存在的 `pnpm test` 已通过记录。
- 不以 mock 连接器替代所有 SQL 结果测试，也不以一次 LLM 回答成功替代确定性回归。
- 环境缺失导致未执行时，记录“未执行”、原因和重跑命令；不能记为通过。继续完成不依赖该环境的工作。
- PR 的 CI 失败必须检查原因并处理；基线已有失败与新引入失败分开报告。

## 11. 发布、观测与回退

受治理查询运行时默认开启；环境变量 `AUTO_ONTOLOGY_GOVERNED_METRICS_ENABLED=false` 或 Helm `backend.governedMetricsEnabled=false` 可显式关闭。开启不替代模型认证，也不将探索性查询自动标记为标准指标结果。当前集成验证在本地工作区进行，真实模型服务和生产业务口径的验收须独立记录。

建议启用顺序：

1. P0 迁移和候选保真修复；候选仍按原治理流程审核。
2. 使用已注册测试契约完成 P1，比较数据库结果和独立基准。
3. 在获准的业务样本上验证；需要影子执行时限制额外查询量。
4. P2 开放管理与口径选择，再按实际授权启用业务查询。

必要观测：

- 请求路径、契约/依赖版本、选源、计划 ID、实际执行状态。
- 澄清、拒绝、来源失效、单位冲突、基数风险的原因分布。
- SQL 执行与结果验证的真实耗时；不使用随机数或演示耗时。
- 不在日志中无差别记录用户数据、凭据或完整敏感参数。

回退优先关闭新增路由，并恢复先前有效契约指向；保留已有版本和诊断证据。已激活契约变化采用新版本或指针回退，不原地篡改已认证版本。涉及删表的 schema downgrade 仅在有备份、明确数据影响且获得对应授权时执行。

## 12. 最终交付物与完成定义

实施 agent 应交付：

- [ ] 代码提交/PR，按 P0/P1/P2 标明实际完成范围。
- [ ] 基线与 ADR：目标提交、首版能力、模型/API/选源/空值等关键决策。
- [ ] 增量迁移、升级说明、功能开关和回退步骤。
- [ ] 契约 DTO/API 规范、原生契约包与往返验证。
- [ ] F0 与独立扩展 fixture、基准 SQL、测试命令和结果。
- [ ] T01–T24 逐项报告：通过、失败、未执行；附证据和原因。
- [ ] 旧查询与权限的兼容性验证。
- [ ] 前端关键流程验证及必要截图。
- [ ] 已知限制及后续扩展清单，不把预留字段当作已支持功能。
- [ ] 简短交付说明：改了什么、解决什么、怎么验证、剩余限制。

完成定义：P0/P1/P2 的阶段门槛均满足，首版支持范围可实际执行，不支持范围可一致识别；自动检查、数据库结果、界面及版本治理能够相互对应。任一关键项未验证，报告应明确其状态，不用“代码已写完”代替完成。

## 13. Agent 启动指令

将本文件放入目标开发仓库，例如 `docs/plans/auto-ontology-semantic-layer-plan.md`，并向 agent 下达：

> 以本任务书为实施依据，在当前 Auto Ontology 开发仓库增量实现。先完成 S0 并记录实际版本与差异，然后按 P0 → P1 → P2 持续推进。先建立能暴露现有问题的测试，再实现修复。复用现有数据源、身份、认证、执行和前端体系；不照搬 Playground 的查询翻译代码。每阶段交付代码、必要文档和可复现验证结果。常规实现选择自行决定；真正缺少业务口径时记录阻塞项并继续可独立推进的工作。不要只返回新的规划，完成本任务书定义的首版，并如实区分通过、失败和未执行。代码提交、远端 PR、生产发布及业务认证遵循当前会话授权。

## 14. 源码依据

以下链接均固定于调研提交；实现时需与目标 HEAD 核对。

1. [Auto 总体能力与现有查询路径](https://github.com/NVIDIA/auto-ontology/blob/fc43a82600e18b19d159c52023f54f5572f537ac/README.md)
2. [Auto 开发规范](https://github.com/NVIDIA/auto-ontology/blob/fc43a82600e18b19d159c52023f54f5572f537ac/CLAUDE.md)
3. [Auto SQLAttribute 提炼与包装](https://github.com/NVIDIA/auto-ontology/blob/fc43a82600e18b19d159c52023f54f5572f537ac/auto_ontology/semantic/sql_attribute_suggester.py#L301)
4. [Auto schema 与认证/物理映射](https://github.com/NVIDIA/auto-ontology/blob/fc43a82600e18b19d159c52023f54f5572f537ac/auto_ontology/dal/schema.py)
5. [Auto SQLAttribute 服务](https://github.com/NVIDIA/auto-ontology/blob/fc43a82600e18b19d159c52023f54f5572f537ac/auto_ontology/server/sql_attributes/service.py)
6. [Auto 官方业务建模原则](https://github.com/NVIDIA/auto-ontology/blob/fc43a82600e18b19d159c52023f54f5572f537ac/skills/nvidia-ontology-management/references/modeling.md)
7. [Playground Metric、Relationship、ObjectDataSource](https://github.com/zao-sheng/Ontology-Playground-zh-dev/blob/a7dc01dc03ad74277de83c7bf99a0655c82d2b4b/src/data/ontology.ts)
8. [Playground 设计器校验](https://github.com/zao-sheng/Ontology-Playground-zh-dev/blob/a7dc01dc03ad74277de83c7bf99a0655c82d2b4b/src/store/designerStore.ts#L78)
9. [Playground MQL 协议](https://github.com/zao-sheng/Ontology-Playground-zh-dev/blob/a7dc01dc03ad74277de83c7bf99a0655c82d2b4b/src/oag/types.ts)
10. [Playground 翻译器及已识别实现边界](https://github.com/zao-sheng/Ontology-Playground-zh-dev/blob/a7dc01dc03ad74277de83c7bf99a0655c82d2b4b/src/oag/translator.ts)
11. [Playground 消歧与演示规则](https://github.com/zao-sheng/Ontology-Playground-zh-dev/blob/a7dc01dc03ad74277de83c7bf99a0655c82d2b4b/src/oag/mockOag.ts)
12. [Playground 图库补全逻辑](https://github.com/zao-sheng/Ontology-Playground-zh-dev/blob/a7dc01dc03ad74277de83c7bf99a0655c82d2b4b/scripts/enrich-catalogue.ts)

业务样例依据：已有《面向 Auto Ontology 的数据建模规范》v3.0。为便于交接，本任务书已内嵌基础表结构、完整样例记录、预期结果与独立补充夹具，实施不依赖先前聊天内容。
