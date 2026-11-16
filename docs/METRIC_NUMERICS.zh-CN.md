<!--
SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# 指标计算与物理认证边界

PostgreSQL 物理指纹升级为 v2。生成列公式、原生类型、collation 及其实际版本、表的行安全属性进入指纹；普通注释不进入。旧证书不能自动续用，必须创建新修订，验证、业务复核并重新认证。

PostgreSQL 关联要求相同原生比较类型，或整数/numeric 之间的精确转换，并要求相同 collation。唯一性证明只接受与列 collation 一致的内建默认 B-tree operator class；不以自定义或 pattern operator class 的唯一索引替代证明。当前治理范围是无继承的普通表；生成列使用自定义函数或操作符时明确拒绝，避免函数实现改变后证书仍有效。

PG 四则运算在执行操作之前提升到 numeric。MySQL 使用 scale 为零的 decimal 零值将整数操作提升到精确 decimal 运算，保留原生 decimal 小数位，不统一转换成 DECIMAL(65,30)。认证时根据物理字段和表达式推导小数位；超过 30 位的精确表达式拒绝认证，要求明确的业务舍入阶段。除法使用治理会话规定的 30 位精度，超出数据库数值范围返回 `NUMERIC_RANGE_EXCEEDED`。

真实 SQL NULL 保留为 null。float 或 Decimal 的 NaN、Infinity、-Infinity 返回 `NON_FINITE_RESULT`，不能返回 trusted 数值结果，也不能静默改成 NULL。

币种分组检查完整角色路径。返回已访问来源的循环路径不能充当原事实币种；需要多个角色时，注册不同的来源角色并明确关系。MySQL 查询分组时区检查使用 `[start, end_exclusive)`；源 DATETIME 的边界转换仍要求无歧义，不能放松墙上时间的 fold/gap 约束。

验证使用隔离 PostgreSQL 16.2 和 MySQL 5.7.44，不是用户生产数据。覆盖 2^53 关联扇出、整数乘法、unsigned 减法、65 位 decimal、30 位小数、生成公式变更/重新认证、非有限数、循环币种路径和夏令时上界。

## 角色过滤与依赖认证

过滤器支持可选 `relationship_path`。省略时只接受唯一可推导路径；空数组表示当前事实；非空数组按顺序列出已注册关系。查询面板可按注册维度选择买方/卖方等角色，同一字段可使用不同路径组合过滤。路径必须连续、无循环且不会扩展事实行。

新证书保存每个来源的审核指纹。执行先从不可变模型编译完整计划，只检查计划包含的来源、关系和指标依赖；预检后，在同一只读快照中锁定实际关系、重新比较指纹并执行。无关表变化不再阻止该查询，实际依赖变化仍拒绝；不会依据源库故障重新挑选不同事实来源。解释自然语言时只读取已认证定义，执行时才获得对应依赖的有效性证明。

新增 Alembic revision `a1de07be4353` 为旧证书写入空依赖映射。旧证书继续使用完整模型指纹检查，不从当前数据库推导新的审核权限；新建并审核的修订才使用依赖级验证。Helm 启动门禁版本已同步。
