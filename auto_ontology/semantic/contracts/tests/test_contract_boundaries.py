# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Contract boundaries that must fail before a database is contacted."""

import pytest

from auto_ontology.semantic.contracts.compiler import compile_query
from auto_ontology.semantic.contracts.models import (
    ContractError,
    GovernedModel,
    MetricQueryRequest,
)
from auto_ontology.semantic.contracts.tests.fixtures import f0_model, field, ref, source
from auto_ontology.semantic.contracts.tests.test_compiler_results import (
    employee_model,
    ratio_model,
    rebuild,
    request,
    scoped_money_model,
)


def test_composite_empty_value_cannot_be_silently_ignored() -> None:
    model = rebuild(
        ratio_model("ontology_fixture"),
        lambda data: data["metrics"][-1].update(empty_value="zero"),
    )
    with pytest.raises(ContractError):
        compile_query(model, MetricQueryRequest(metric_ids=("ratio",), all_time=True))


@pytest.mark.parametrize("aggregation", ["sum", "count", "count_distinct"])
def test_arithmetic_requires_numeric_operands_for_every_aggregate(
    aggregation: str,
) -> None:
    def change(data: dict) -> None:
        data["sources"][0]["fields"].append(field("label", "string"))
        data["metrics"][0].update(
            aggregation=aggregation,
            expression={
                "kind": "binary",
                "operator": "multiply",
                "left": {"kind": "field", "field_id": "label"},
                "right": {"kind": "number", "value": "2"},
            },
        )

    with pytest.raises(ContractError):
        compile_query(rebuild(f0_model(), change), request())


@pytest.mark.parametrize("identifier", ["m" * 64, "额" * 22])
def test_metric_output_alias_cannot_exceed_postgres_utf8_byte_limit(
    identifier: str,
) -> None:
    model = rebuild(
        f0_model(), lambda data: data["metrics"][0].update(metric_id=identifier)
    )
    with pytest.raises(ContractError):
        compile_query(model, request(metric_ids=[identifier]))


@pytest.mark.parametrize("identifier", ["d" * 64, "维" * 22])
def test_dimension_output_alias_cannot_exceed_postgres_utf8_byte_limit(
    identifier: str,
) -> None:
    def change(data: dict) -> None:
        data["sources"][0]["dimensions"][0]["dimension_id"] = identifier
        for metric in data["metrics"]:
            metric["supported_dimensions"][0] = identifier

    with pytest.raises(ContractError):
        compile_query(rebuild(f0_model(), change), request(dimensions=[identifier]))


def test_postgres_63_byte_alias_is_allowed() -> None:
    identifier = "额" * 21
    model = rebuild(
        f0_model(), lambda data: data["metrics"][0].update(metric_id=identifier)
    )
    assert compile_query(model, request(metric_ids=[identifier])).sql


def test_currency_metric_requires_a_currency_binding() -> None:
    model = rebuild(
        f0_model(),
        lambda data: data["metrics"][0].update(currency_field_id=None),
    )
    with pytest.raises(ContractError):
        compile_query(model, request())


def test_same_owner_does_not_allow_distinct_fact_sources() -> None:
    def change(data: dict) -> None:
        other = source(
            "other_fact",
            "other_fact",
            ["id"],
            [field("id"), field("amount")],
            "ontology_fixture",
        )
        other["owner_term_id"] = "lines"
        data["sources"].append(other)
        data["metrics"].append(
            {
                "metric_id": "other_amount",
                "name": "other_amount",
                "description": "相同业务owner上的独立事实",
                "owner_term_id": "lines",
                "source_ids": ["other_fact"],
                "aggregation": "sum",
                "expression": {"kind": "field", "field_id": "amount"},
                "unit": "number",
                "supported_dimensions": ["currency_code"],
            }
        )

    with pytest.raises(ContractError):
        compile_query(
            rebuild(f0_model(), change),
            request(metric_ids=["confirmed_amount", "other_amount"]),
        )


def test_different_business_owners_can_share_one_fact_binding() -> None:
    model = rebuild(
        f0_model(),
        lambda data: data["metrics"][1].update(owner_term_id="another_business_owner"),
    )
    assert compile_query(
        model, request(metric_ids=["confirmed_amount", "cancelled_amount"])
    ).sql


def candidate_model(
    *, changed_grain: bool, changed_filter: bool, pre_aggregated: bool
) -> GovernedModel:
    def change(data: dict) -> None:
        alternative = {
            **data["sources"][0],
            "source_id": "alternate_ratios",
            "catalog_table_id": "alternate-table",
            "table_name": "alternate_ratio_rows",
            "priority": 0,
            "pre_aggregated": pre_aggregated,
        }
        if changed_grain:
            alternative["grain"] = ["numerator"]
        if changed_filter:
            alternative["required_filters"] = [
                {
                    "field": ref("alternate_ratios", "numerator"),
                    "operator": "gt",
                    "value": 0,
                }
            ]
        data["sources"].append(alternative)
        for metric in data["metrics"]:
            metric["source_ids"].append("alternate_ratios")

    return rebuild(ratio_model("ontology_fixture"), change)


@pytest.mark.parametrize(
    ("changed_grain", "changed_filter"), [(True, False), (False, True)]
)
def test_alternative_sources_cannot_change_grain_or_mandatory_scope(
    changed_grain: bool,
    changed_filter: bool,
) -> None:
    model = candidate_model(
        changed_grain=changed_grain,
        changed_filter=changed_filter,
        pre_aggregated=False,
    )
    with pytest.raises(ContractError):
        compile_query(model, MetricQueryRequest(metric_ids=("ratio",), all_time=True))


def test_excluded_preaggregate_does_not_poison_detail_source() -> None:
    model = candidate_model(
        changed_grain=True, changed_filter=True, pre_aggregated=True
    )
    plan = compile_query(
        model, MetricQueryRequest(metric_ids=("ratio",), all_time=True)
    )
    assert plan.evidence["selected_source_id"] == "ratios"


def test_reciprocal_currency_is_explicitly_unsupported() -> None:
    def change(data: dict) -> None:
        data["metrics"].append(
            {
                "metric_id": "reciprocal_amount",
                "name": "reciprocal_amount",
                "description": "首版不支持倒数货币单位",
                "owner_term_id": "lines",
                "source_ids": ["lines"],
                "kind": "composite",
                "expression": {
                    "kind": "binary",
                    "operator": "divide",
                    "left": {"kind": "number", "value": "1"},
                    "right": {"kind": "metric", "metric_id": "confirmed_amount"},
                },
                "supported_dimensions": ["currency_code"],
                "unit": "1/currency",
                "currency_field_id": "currency_code",
            }
        )

    with pytest.raises(ContractError):
        compile_query(
            rebuild(f0_model(), change), request(metric_ids=["reciprocal_amount"])
        )


def test_self_relationship_requires_distinct_role_bindings() -> None:
    with pytest.raises(ContractError) as error:
        compile_query(
            employee_model("ontology_fixture", distinct_binding=False),
            MetricQueryRequest(
                metric_ids=("employee_amount",),
                dimensions=("manager_name",),
                all_time=True,
            ),
        )
    assert "SELF" in error.value.code


def test_currency_field_with_multiple_role_paths_is_ambiguous() -> None:
    def change(data: dict) -> None:
        data["relationships"].append(
            {
                **data["relationships"][0],
                "relationship_id": "alternative_order_role",
                "role": "alternative_order",
                "keys": [
                    {"source_field_id": "product_id", "target_field_id": "order_id"}
                ],
            }
        )
        data["sources"][0]["dimensions"].append(
            {
                "dimension_id": "alternative_currency",
                "field": ref("orders", "currency_code"),
                "relationship_path": ["alternative_order_role"],
            }
        )
        for metric in data["metrics"]:
            metric["supported_dimensions"].append("alternative_currency")

    with pytest.raises(ContractError):
        compile_query(
            rebuild(f0_model(), change), request(dimensions=["alternative_currency"])
        )


@pytest.mark.parametrize("value", [True, "NaN", "Infinity", "-Infinity"])
def test_numeric_filter_rejects_boolean_and_nonfinite_text(value: object) -> None:
    with pytest.raises(ContractError):
        compile_query(
            f0_model(),
            request(
                filters=[
                    {
                        "field": ref("lines", "quantity"),
                        "operator": "eq",
                        "value": value,
                    }
                ]
            ),
        )


def test_alternative_sources_cannot_change_filter_field_type() -> None:
    def change(data: dict) -> None:
        data["sources"][0]["fields"].append(field("code", "string"))
        data["sources"][1]["fields"].append(field("code", "number"))
        data["metrics"][0]["required_filters"] = [
            {"field": ref("ratios", "code"), "operator": "gt", "value": "10"}
        ]

    model = candidate_model(
        changed_grain=False, changed_filter=False, pre_aggregated=False
    )
    with pytest.raises(ContractError):
        compile_query(
            rebuild(model, change),
            MetricQueryRequest(metric_ids=("ratio",), all_time=True),
        )


def test_alternative_sources_cannot_change_dimension_result_type() -> None:
    def change(data: dict) -> None:
        for binding in data["sources"]:
            binding["fields"].extend(
                [field("group_text", "string"), field("group_number")]
            )
            field_id = (
                "group_text" if binding["source_id"] == "ratios" else "group_number"
            )
            binding["dimensions"] = [
                {"dimension_id": "group", "field": ref(binding["source_id"], field_id)}
            ]
        for metric in data["metrics"]:
            metric["supported_dimensions"] = ["group"]

    model = candidate_model(
        changed_grain=False, changed_filter=False, pre_aggregated=False
    )
    with pytest.raises(ContractError):
        compile_query(
            rebuild(model, change),
            MetricQueryRequest(
                metric_ids=("ratio",), dimensions=("group",), all_time=True
            ),
        )


@pytest.mark.parametrize("operator", ["add", "divide"])
def test_different_single_currency_operands_cannot_be_combined(operator: str) -> None:
    model = scoped_money_model(
        f0_model(), left_currency="CNY", right_currency="USD", operator=operator
    )
    with pytest.raises(ContractError) as error:
        compile_query(model, request(metric_ids=["scoped_composite"], dimensions=[]))
    assert error.value.code == "UNIT_MISMATCH"


def test_nullable_currency_groups_require_explicit_handling() -> None:
    def change(data: dict) -> None:
        for binding in data["sources"][1]["fields"]:
            if binding["field_id"] == "currency_code":
                binding["nullable"] = True

    with pytest.raises(ContractError) as error:
        compile_query(rebuild(f0_model(), change), request())
    assert error.value.code == "UNKNOWN_CURRENCY"


def test_left_join_cannot_create_unknown_currency_group() -> None:
    def change(data: dict) -> None:
        data["relationships"][0]["join_type"] = "left"
        data["metrics"][0]["required_filters"] = []

    with pytest.raises(ContractError) as error:
        compile_query(rebuild(f0_model(), change), request())
    assert error.value.code == "UNKNOWN_CURRENCY"
