<!--
SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# Chat SQL 只读边界与结果预算

探索查询和数据库探测默认经过同一个确定性检查。该边界始终启用，不由模型判断是否绕过。

## 查询规则

- 只接受一个 SELECT 或集合查询；所有嵌套层级都检查写 CTE、DML、DDL、SELECT INTO、锁定读取、会话变量和执行型注释。
- 函数使用经过审查的允许列表。未知函数、自定义函数、带 schema 的函数调用、文件和网络表函数不能通过探索入口执行。
- SQL 校验节点在访问业务库或调用意图模型前拒绝不安全语句；执行和探测入口再次检查。拒绝不会进入“请模型决定是否允许”的重试分支。
- PostgreSQL、MySQL、SQLite、DuckDB 使用数据库只读事务或只读连接。原有受信任的摄取/管理 execute() 保留独立接口，不接收探索模型的任意输出。
- 查询日志只记录指纹、长度及方言，不输出 SQL 字面量或业务库名称。本条不代表其他所有日志入口已经完成审计。

只读事务不等于任意数据库函数都无副作用，也不替代源数据库最小权限。业务连接应使用独立读取账号：授予必要表的 SELECT 和必要 schema 的 USAGE，不使用数据库所有者或管理员；撤销业务过程、外部访问扩展和非必要函数的 EXECUTE。MySQL 账号不授予 FILE、EXECUTE、SUPER 或写表权限，并保持 TLS 身份校验。视图及自定义类型也应纳入数据源发布审查。

## 交互结果

默认最多保留 1000 行、512 KiB 序列化预算、单行 64 KiB 和 128 列。预算按服务端常量执行；探测查询使用更小的行数/总大小预算。源 SQL 的较小 LIMIT 保留，较大 LIMIT 或缺失 LIMIT 由最外层结果限制覆盖；不会误把子查询的 LIMIT 当成整体限制。

结果携带 result_metadata：truncated、reason、returned_rows、returned_bytes、row_limit、byte_limit。字节数是包含列名和转义开销的保守计量。超过总行数或总字节数时返回前缀结果，并明确显示不完整；单行/列元数据超限直接拒绝，不能悄悄把一个超大值替换成 NULL。

部分结果不会交给图表模型按完整数据解释。答案及结果表的截断提示写入原有会话，因此刷新、重连或再次打开会话后仍可看到。需要完整总体值时，缩小筛选范围或使用聚合查询；当前没有把大结果下载伪装成完整导出。

| 引擎 | 读取与内存边界 |
| --- | --- |
| PostgreSQL | READ ONLY 快照；LIMIT 0 获取列元数据；命名服务器游标逐行读取；服务端先检查位置 JSON 的字节数，防止单个超大单元格进入 libpq 缓冲。保留重复列名、Decimal、空列及 NULL。 |
| MySQL 5.7+ | READ ONLY 事务；非缓冲游标；协议包头声明的长度在分配包体前检查。超限或提前结束不会继续排空大结果，而是结束该服务端连接。TLS 配置沿用正式连接。 |
| SQLite | 独立 mode=ro 连接、query_only、引擎值长度/列数上限和逐行读取。 |
| DuckDB | READ ONLY 事务、最外层行数限制和逐行收集。引擎在进程内计算的内存仍由部署 memory_limit/容器预算约束。 |
| 其他仓库连接器 | 共用 AST、最外层行数和序列化预算。当前仍使用原有驱动读取接口；不能把这些路径宣称为具有超大单行的分配前内存保证。 |

PostgreSQL/MySQL 的大结果与取消测试使用隔离数据库和合成数据。测试覆盖百万行、64 MiB 单个文本值、写权限账号及有写副作用的函数、Decimal/重复列名/NULL、超时、实际服务端查询清理。此验证不涉及用户生产数据、真实 LLM 或真实 SSO 登录。

技术依据：[Psycopg 游标与结果缓冲](https://www.psycopg.org/psycopg3/docs/advanced/cursors.html)、[MySQL 只读事务接口](https://dev.mysql.com/doc/connector-python/en/connector-python-api-mysqlconnection-start-transaction.html)。MySQL 协议读取适配器由当前锁定版本和真实 5.7 回归约束，驱动传输 API 改变时拒绝执行，不静默放弃预算。
