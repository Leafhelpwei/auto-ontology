<!--
SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# Chat 运行协调与恢复

后端使用 PostgreSQL 的 chat_run / chat_run_event 保存运行状态和事件。
这套协调默认启用，不依赖负载均衡的会话粘性。升级前执行项目现有的
alembic upgrade head；Helm 的数据库迁移与版本等待任务已同步到新 revision。

## 一致性与故障行为

- 在启动查询子进程前，数据库完成会话独占、全局配额和用户配额检查。同一会话冲突返回 409，配额已满返回 429，工作进程或运行存储不可用返回 503。
- 运行获得服务器生成的 ID 和独占租约。所有事件、对应的会话消息及分析记录在同一事务内写入；过期 owner token 无法写入。
- 关闭浏览器只断开观察者。POST /api/chat/cancel 将取消请求写入共享数据库，执行副本每秒以内轮询并终止子进程；数据库查询的实际停止仍受各连接器的取消机制约束。
- 执行副本崩溃或租约超时后，其他副本把任务记为 interrupted，并持久化中断原因。不会自动重新执行未知完成状态的查询。
- GET /api/chat/watch 默认观察当前运行；请求头 Last-Event-ID 使用“运行 UUID:序号”从断点继续，也可读取保留期内已完成的运行。游标越界返回 422，用户或会话不匹配返回 404。
- 普通页面恢复先读取已有会话，再观察当前运行；不会反复重放已完成的旧任务。Web 代理转发游标和运行 ID，断流时释放观察者，不需要额外复制一份 SSE 数据流。
- 连接配置写入和全局 generation 更新在同一 PostgreSQL 事务完成。各工作池在取用和归还进程时检查 generation；旧配置进程不能再次成为可用实例。Vault 写入仍通过原有连接管理 API，外部直接修改 Vault 时也需经该 API 刷新版本。

## 预算

| 环境变量 | 默认值 | 作用域 |
| --- | --- | --- |
| CHAT_MAX_ACTIVE_RUNS | CHAT_MAX_WORKERS，默认 4 | 整个部署的运行数，包括等待初始化的任务 |
| CHAT_MAX_RUNS_PER_USER | 2 | 整个部署；无用户的私有 API 请求共享匿名配额 |
| CHAT_MAX_WORKERS | 4 | 每个后端进程的查询子进程上限，包括预热实例 |
| CHAT_WORKER_STARTUP_TIMEOUT_S | 60 | 子进程初始化截止时间 |
| CHAT_REQUEST_TIMEOUT_S | 180 | 子进程执行截止时间 |
| CHAT_RUN_TIMEOUT_S | 300 | 从占位到最终持久化的运行总期限 |
| CHAT_LEASE_SECONDS | 15 | 执行副本的租约时间；最低 3 秒 |
| CHAT_MAX_EVENT_BYTES | 2097152 | 单个普通 IPC / 重放事件上限 |
| CHAT_MAX_RUN_BYTES | 8388608 | 单轮重放日志预算，预留 4096 字节用于终态 |
| CHAT_MAX_EVENTS | 1024 | 单轮事件数量上限，包含终态预留 |
| CHAT_EVENT_RETENTION_SECONDS | 86400 | 已完成事件日志保留时间；会话消息独立保留 |

同一部署的后端副本必须使用一致的全局配额和租约配置。查询子进程容量上界为
“后端进程数 × CHAT_MAX_WORKERS”，包括空闲预热进程；数据库连接预算还需要乘上
各连接器及向量存储的连接池大小。增加 backend.replicas 不会增加全局运行配额。

超过交互预算的任务会终止并要求缩小查询范围。事件预算不替代源数据库的流式读取、
行数限制、最小权限或容器内存上限。源查询的实际限制与各引擎验证范围见 [交互 SQL 说明](INTERACTIVE_SQL.zh-CN.md)。

## 验证边界

回归测试启动两个真实 Uvicorn 进程、spawn 查询子进程及隔离 PostgreSQL，覆盖并发独占、
跨副本观察/取消、执行副本 SIGKILL、租约过期后的迟到写入、断点重放、预算和历史一致性。
连接版本测试覆盖忙碌实例与另一工作池的预热实例、凭据轮换及连接删除。

测试中的查询计算使用固定输出以隔离运行协议；该测试不代表真实 LLM 问数质量或用户
生产数据验证。真实 PostgreSQL 和 MySQL 5.7 查询取消由独立连接器测试覆盖。
