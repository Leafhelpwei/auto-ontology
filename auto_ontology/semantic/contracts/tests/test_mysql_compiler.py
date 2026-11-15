# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Dialect boundaries and backward-compatible PostgreSQL contract identity."""

from __future__ import annotations

from datetime import datetime

import pytest

from auto_ontology.semantic.contracts.compiler import compile_query, quote
from auto_ontology.semantic.contracts.models import (
    ContractError,
    GovernedModel,
    MetricQueryRequest,
)
from auto_ontology.semantic.contracts.tests.fixtures import f0_model
from auto_ontology.semantic.contracts.validator import validate_model


def mysql_model(zone: str = "Asia/Shanghai") -> GovernedModel:
    data = f0_model().model_dump(mode="json")
    data["dialect"] = "mysql"
    for source in data["sources"]:
        for field in source["fields"]:
            if field["data_type"] == "datetime":
                field["datetime_timezone"] = zone
    return GovernedModel.model_validate(data)


def timed_model(zone: str = "Asia/Shanghai") -> GovernedModel:
    model = mysql_model(zone)
    return model.model_copy(
        update={
            "metrics": tuple(
                metric.model_copy(
                    update={
                        "time_field_id": "created_at",
                        "time_role": "created",
                        "supported_granularities": ("day", "month", "year"),
                    }
                )
                for metric in model.metrics
            )
        }
    )


def time_request(
    start: str, end: str, zone: str = "UTC", granularity: str | None = "day"
) -> MetricQueryRequest:
    return MetricQueryRequest.model_validate(
        {
            "metric_ids": ["confirmed_amount"],
            "dimensions": ["currency_code"],
            "time": {
                "start": start,
                "end_exclusive": end,
                "timezone": zone,
                "time_role": "created",
                "granularity": granularity,
            },
        }
    )


def test_existing_postgres_fingerprint_is_unchanged() -> None:
    # Calculated from models.py at pre-MySQL HEAD, not the implementation under test.
    assert (
        f0_model().fingerprint()
        == "7d554d480d2e6d9867adb9294cbbaede5f1abe199bcdcc566032c9e50029832c"
    )
    assert "datetime_timezone" not in f0_model().model_dump_json()
    data = f0_model().model_dump(mode="json")
    data["sources"][1]["fields"][4]["datetime_timezone"] = None
    assert GovernedModel.model_validate(data).fingerprint() == f0_model().fingerprint()


def test_mysql_uses_only_57_supported_relational_constructs() -> None:
    plan = compile_query(
        mysql_model(),
        MetricQueryRequest(
            metric_ids=("confirmed_amount", "cancelled_amount"),
            dimensions=("currency_code", "customer_name"),
            all_time=True,
        ),
    )
    assert plan.sql.startswith("SELECT ")
    assert "<=>" in plan.sql and " UNION " in plan.sql
    assert all(
        token not in plan.sql
        for token in ("WITH ", "FULL OUTER", "OVER (", "NULLS LAST", "TIMESTAMPTZ")
    )
    assert plan.evidence["mysql_session"]["div_precision_increment"] == 30


@pytest.mark.parametrize("zone", ["UTC", "+08:00", "Asia/Shanghai"])
def test_mysql_time_bounds_are_explicit_naive_storage_values(zone: str) -> None:
    plan = compile_query(
        timed_model(zone), time_request("2026-11-01T00:00:00Z", "2026-11-02T00:00:00Z")
    )
    bounds = [value for value in plan.params.values() if isinstance(value, datetime)]
    assert len(bounds) == 2 and all(value.tzinfo is None for value in bounds)
    assert bounds[0].hour == (0 if zone == "UTC" else 8)
    assert "CONVERT_TZ" not in plan.sql
    assert "DATE_FORMAT" in plan.sql and "%Y-%m-%d 00:00:00" in plan.params.values()


@pytest.mark.parametrize(
    ("start", "end"),
    [
        ("2026-03-01T00:00:00Z", "2026-04-01T00:00:00Z"),
        ("2026-01-01T00:00:00Z", "2026-12-31T00:00:00Z"),
        ("2026-11-01T05:10:00Z", "2026-11-01T05:20:00Z"),
    ],
)
def test_source_dst_transition_or_fold_is_rejected(start: str, end: str) -> None:
    with pytest.raises(ContractError) as error:
        compile_query(timed_model("America/New_York"), time_request(start, end))
    assert error.value.code == "UNSUPPORTED_TIMEZONE_TRANSITION"


def test_utc_storage_can_filter_across_dst_but_grouping_must_not_guess() -> None:
    request = time_request(
        "2026-03-01T00:00:00Z", "2026-04-01T00:00:00Z", "America/New_York", None
    )
    compile_query(timed_model("UTC"), request)
    with pytest.raises(ContractError) as error:
        compile_query(
            timed_model("UTC"),
            request.model_copy(
                update={"time": request.time.model_copy(update={"granularity": "day"})}
            ),
        )
    assert error.value.code == "UNSUPPORTED_TIMEZONE_TRANSITION"


@pytest.mark.parametrize(
    ("zone", "code"),
    [
        (None, "DATETIME_TIMEZONE_REQUIRED"),
        ("bad/timezone", "INVALID_TIMEZONE"),
        ("+14:00", "INVALID_TIMEZONE"),
        ("-13:00", "INVALID_TIMEZONE"),
    ],
)
def test_invalid_or_missing_storage_timezone(zone: str | None, code: str) -> None:
    data = mysql_model().model_dump(mode="json")
    data["sources"][1]["fields"][4]["datetime_timezone"] = zone
    result = validate_model(GovernedModel.model_validate(data))
    assert not result.valid and result.issues[0].code == code


def test_postgres_does_not_accept_mysql_storage_override() -> None:
    result = validate_model(mysql_model().model_copy(update={"dialect": "postgres"}))
    assert result.issues[0].code == "INVALID_STORAGE_TIMEZONE"


def test_mysql_percent_identifier_preserves_driver_spelling() -> None:
    assert quote("gross%`amount", "mysql") == "`gross%``amount`"
    assert quote("gross%amount") == '"gross%%amount"'
    with pytest.raises(ContractError):
        quote("gross%(p0)s", "mysql")


def test_mysql_error_division_is_explicitly_unsupported() -> None:
    data = mysql_model().model_dump(mode="json")
    metric = data["metrics"][0]
    metric["expression"]["operator"] = "divide"
    metric["zero_division"] = "error"
    result = validate_model(GovernedModel.model_validate(data))
    assert result.issues[0].code == "UNSUPPORTED_ZERO_DIVISION"


def test_upper_datetime_boundary_does_not_overflow_daily_scan() -> None:
    compile_query(
        timed_model("UTC"),
        time_request("9999-12-31T10:00:00Z", "9999-12-31T11:00:00Z", granularity=None),
    )
    with pytest.raises(ContractError) as error:
        compile_query(
            timed_model("+13:00"),
            time_request(
                "9999-12-31T12:00:00Z", "9999-12-31T13:00:00Z", granularity=None
            ),
        )
    assert error.value.code == "UNSUPPORTED_TIMEZONE_RANGE"
