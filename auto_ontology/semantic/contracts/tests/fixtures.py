# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Independent F0 order data and example contracts used by result tests."""

from auto_ontology.semantic.contracts.models import GovernedModel


def field(name: str, data_type: str = "number", nullable: bool = False) -> dict:
    return {
        "field_id": name,
        "catalog_column_id": f"fixture-column-{name}",
        "column_name": name,
        "data_type": data_type,
        "nullable": nullable,
    }


def ref(source: str, name: str) -> dict:
    return {"source_id": source, "field_id": name}


def source(
    source_id: str, table: str, grain: list[str], fields: list[dict], schema: str
) -> dict:
    return {
        "source_id": source_id,
        "datasource_id": "fixture-postgres",
        "catalog_table_id": f"fixture-table-{table}",
        "owner_term_id": source_id,
        "schema_name": schema,
        "table_name": table,
        "grain": grain,
        "fields": fields,
    }


def f0_model(schema: str = "ontology_fixture") -> GovernedModel:
    """A line-grain fact joins order/customer dimensions without fanout."""
    lines = source(
        "lines",
        "order_line",
        ["order_line_id"],
        [
            field(n)
            for n in [
                "order_line_id",
                "order_id",
                "product_id",
                "quantity",
                "unit_price",
            ]
        ],
        schema,
    )
    orders = source(
        "orders",
        "sales_order",
        ["order_id"],
        [
            field("order_id"),
            field("customer_id"),
            field("status_code", "string"),
            field("currency_code", "string"),
            field("created_at", "datetime"),
            field("confirmed_at", "datetime", True),
        ],
        schema,
    )
    customers = source(
        "customers",
        "customer",
        ["customer_id"],
        [field("customer_id"), field("customer_name", "string", True)],
        schema,
    )
    products = source(
        "products",
        "product",
        ["product_id"],
        [field("product_id"), field("product_name", "string")],
        schema,
    )
    lines["dimensions"] = [
        {
            "dimension_id": "currency_code",
            "field": ref("orders", "currency_code"),
            "relationship_path": ["line_order"],
        },
        {
            "dimension_id": "customer_name",
            "field": ref("customers", "customer_name"),
            "relationship_path": ["line_order", "order_customer"],
        },
        {
            "dimension_id": "product_name",
            "field": ref("products", "product_name"),
            "relationship_path": ["line_product"],
        },
    ]
    relationships = [
        {
            "relationship_id": "line_order",
            "source_id": "lines",
            "target_id": "orders",
            "role": "belongs_to",
            "keys": [{"source_field_id": "order_id", "target_field_id": "order_id"}],
            "cardinality": "many_to_one",
            "join_type": "inner",
        },
        {
            "relationship_id": "order_customer",
            "source_id": "orders",
            "target_id": "customers",
            "role": "buyer",
            "keys": [
                {"source_field_id": "customer_id", "target_field_id": "customer_id"}
            ],
            "cardinality": "many_to_one",
            "join_type": "left",
        },
        {
            "relationship_id": "line_product",
            "source_id": "lines",
            "target_id": "products",
            "role": "product",
            "keys": [
                {"source_field_id": "product_id", "target_field_id": "product_id"}
            ],
            "cardinality": "many_to_one",
            "join_type": "inner",
        },
    ]
    amount = {
        "metric_id": "confirmed_amount",
        "name": "已确认商品金额",
        "description": "当前已确认订单的明细数量乘成交单价，按币种分别计算。",
        "owner_term_id": "lines",
        "source_ids": ["lines"],
        "aggregation": "sum",
        "expression": {
            "kind": "binary",
            "operator": "multiply",
            "left": {"kind": "field", "field_id": "quantity"},
            "right": {"kind": "field", "field_id": "unit_price"},
        },
        "required_filters": [
            {
                "field": ref("orders", "status_code"),
                "operator": "eq",
                "value": "CONFIRMED",
            }
        ],
        "supported_dimensions": ["currency_code", "customer_name", "product_name"],
        "unit": "currency",
        "currency_field_id": "currency_code",
        "empty_value": "null",
    }
    cancelled = {
        **amount,
        "metric_id": "cancelled_amount",
        "name": "已取消商品金额",
        "required_filters": [
            {
                "field": ref("orders", "status_code"),
                "operator": "eq",
                "value": "CANCELLED",
            }
        ],
    }
    count = {
        "metric_id": "confirmed_order_count",
        "name": "已确认订单数",
        "description": "当前已确认且已通过明细完整性校验的订单去重数。",
        "owner_term_id": "lines",
        "source_ids": ["lines"],
        "aggregation": "count_distinct",
        "expression": {"kind": "field", "field_id": "order_id"},
        "required_filters": amount["required_filters"],
        "supported_dimensions": amount["supported_dimensions"],
        "unit": "count",
        "empty_value": "zero",
    }
    return GovernedModel.model_validate(
        {
            "model_id": "fixture-orders",
            "name": "订单语义验收示例",
            "sources": [lines, orders, customers, products],
            "relationships": relationships,
            "metrics": [amount, cancelled, count],
        }
    )


F0_SQL = """
CREATE TABLE customer (
  customer_id bigint PRIMARY KEY, customer_name text
);
CREATE TABLE product (
  product_id bigint PRIMARY KEY, product_name text NOT NULL
);
CREATE TABLE sales_order (
  order_id bigint PRIMARY KEY,
  customer_id bigint REFERENCES customer,
  status_code text NOT NULL,
  currency_code text NOT NULL,
  created_at timestamptz NOT NULL,
  confirmed_at timestamptz
);
CREATE TABLE order_line (
  order_line_id bigint PRIMARY KEY,
  order_id bigint REFERENCES sales_order,
  product_id bigint REFERENCES product,
  quantity numeric NOT NULL, unit_price numeric NOT NULL
);
INSERT INTO customer VALUES (1, '甲客户'), (2, '乙客户'), (3, '暂无订单客户');
INSERT INTO product VALUES (101, '零件甲'), (102, '零件乙');
INSERT INTO sales_order VALUES
(1001,1,'CONFIRMED','CNY','2026-11-01 08:00+08','2026-11-01 09:00+08'),
(1002,2,'CONFIRMED','USD','2026-11-02 08:00+08','2026-11-02 09:00+08'),
(1003,1,'CANCELLED','CNY','2026-11-03 08:00+08','2026-11-03 09:00+08'),
(1004,2,'DRAFT','CNY','2026-11-04 08:00+08',NULL);
INSERT INTO order_line VALUES
(1,1001,101,2,100),(2,1001,102,1,50),(3,1002,101,1,100),(4,1003,102,1,50);
"""
