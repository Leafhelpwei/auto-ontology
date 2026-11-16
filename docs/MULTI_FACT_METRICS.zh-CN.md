<!--
SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# 订单、支付、退款的跨事实指标

组合指标可显式设置 `aggregation_strategy: independent_facts`。编译器分别聚合各原子指标，再按公共维度对齐聚合结果，最后计算比率或差额。订单与支付、退款的明细表不会直接互相连接，因此一笔订单多次支付不会重复累计订单金额。默认的 `single_fact` 保留原有约束；已有认证模型不会自动改用新的计算方式。

## 建模要求

- 每个原子指标声明自己的事实来源、粒度、固定条件和业务时间；组合指标的 `source_ids` 包含依赖指标的全部候选来源。
- 各事实使用同一数据库和只读执行快照。跨数据库拼接、预聚合来源暂不支持。
- 公共维度必须经安全关系路径解析到同一个注册字段。例如，各事实的客户维度都连接到 `customers.customer_id`；字段名称相同不足以证明业务身份相同。
- 多币种金额须按共同的规范币种字段分组，或满足已有固定币种约束。不会隐式换汇。
- 缺失事实遵循原子指标的 `empty_value`，零分母遵循组合指标的 `zero_division`；不会统一把所有缺失值改为零。

在指标编辑器中，组合指标的 **Fact aggregation** 选择分别聚合后对齐。仍需保存修订、校验、认证和激活，执行入口与单事实指标相同。

## 时间含义

查询订单、支付、退款的组合指标时，时间区间可分别应用于下单时间、支付时间和退款时间。请求的 `time.metric_roles` 必须精确覆盖所有原子依赖，例如：

```json
{
  "time": {
    "start": "2026-01-01T00:00:00Z",
    "end_exclusive": "2026-02-01T00:00:00Z",
    "timezone": "UTC",
    "time_role": "per_metric",
    "metric_roles": {
      "orders_amount": "order_created",
      "payments_amount": "payment_paid",
      "refunds_amount": "refund_processed"
    },
    "granularity": "day"
  }
}
```

这表示每日下单金额与当日支付、退款金额的比较，**不是**某日下单订单的全生命周期回款率。后者需要单独建模订单群组与支付归属。查询界面提供明确的逐指标时间选择，并在指标选择变化时清除之前的逐指标时间确认。

## 执行与验证

PostgreSQL 使用独立聚合 CTE；MySQL 5.7 使用派生表、分组键 UNION 和 NULL 安全连接，不依赖 CTE 或 FULL OUTER JOIN。保留原认证、依赖指纹、物理结构检查、来源优先级和数值规则；物理执行失败不会重新挑选来源。

本实现借鉴分阶段聚合思想，没有引入 MetricFlow 代码或适配器，不新增其运行或许可依赖。隔离 PostgreSQL 16.2、MySQL Community 5.7.44 已执行订单多次支付、跨币种、缺失事实、零分母、嵌套组合、半开时间区间和逐业务事件日分组的独立 Decimal 金标准，并测试创建→校验→认证→激活→执行完整治理链路。真实供应商 LLM 与用户生产数据库不在这批验证范围。
