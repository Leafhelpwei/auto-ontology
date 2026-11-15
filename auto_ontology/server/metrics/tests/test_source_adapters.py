# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Physical binding invariants across PostgreSQL and MySQL source adapters."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pandas as pd
import pytest

from auto_ontology.semantic.contracts.models import (
    ContractError,
    FieldBinding,
    SourceBinding,
)
from auto_ontology.server.metrics import source_adapters as adapters
from auto_ontology.server.metrics.service import serialize_frame


def field(
    name: str = "id", dtype: str = "number", timezone: str | None = None
) -> FieldBinding:
    return FieldBinding(
        field_id=name,
        column_name=name,
        catalog_column_id=f"col-{name}",
        data_type=dtype,
        nullable=False,
        datetime_timezone=timezone,
    )


def source() -> SourceBinding:
    return SourceBinding(
        source_id="orders",
        datasource_id="db-id",
        catalog_table_id="table-id",
        owner_term_id="term-id",
        schema_name="business",
        table_name="orders",
        grain=("id",),
        fields=(field(),),
    )


def fake_mysql_read(
    *,
    engine: str = "InnoDB",
    version: str = "5.7.44",
    session_changes: dict | None = None,
):
    session = {
        "database_name": "business",
        "time_zone": "+00:00",
        "div_precision_increment": 30,
        "sql_mode": "STRICT_ALL_TABLES,ONLY_FULL_GROUP_BY,NO_ZERO_DATE",
        "version": version,
        **(session_changes or {}),
    }
    results = {
        adapters._MYSQL_TABLE_SQL: [
            {
                "schema_name": "business",
                "table_name": "orders",
                "table_type": "BASE TABLE",
                "engine": engine,
                "collation": "utf8mb4_bin",
            }
        ],
        adapters._MYSQL_COLUMNS_SQL: [
            {
                "name": "id",
                "data_type": "bigint(20) unsigned",
                "nullable": "NO",
                "character_set": None,
                "collation": None,
                "extra": "",
                "generation_expression": "",
            }
        ],
        adapters._MYSQL_SESSION_SQL: [session],
        adapters._MYSQL_UNIQUE_SQL: [
            {
                "index_name": "composite",
                "column_name": "tenant_id",
                "key_position": 1,
                "prefix_length": None,
                "index_type": "BTREE",
            },
            {
                "index_name": "composite",
                "column_name": "id",
                "key_position": 2,
                "prefix_length": None,
                "index_type": "BTREE",
            },
            {
                "index_name": "prefix",
                "column_name": "name",
                "key_position": 1,
                "prefix_length": 5,
                "index_type": "BTREE",
            },
        ],
    }

    def read(sql: str, params: Any) -> pd.DataFrame:
        if params is not None:
            assert params == {"schema": "business", "table": "orders"}
        return pd.DataFrame(results[sql], dtype=object)

    return read


def test_mysql_inspection_retains_physical_semantics_and_complete_unique_keys() -> None:
    result = adapters.MySQLSourceAdapter().inspect(fake_mysql_read(), source())
    assert result.unique_keys == [["tenant_id", "id"]]
    assert result.columns[0]["data_type"] == "bigint(20) unsigned"
    assert result.columns[0]["nullable"] is False
    assert result.properties["table"]["collation"] == "utf8mb4_bin"
    assert result.properties["div_precision_increment"] == 30


@pytest.mark.parametrize(
    "changes",
    [
        {"time_zone": "SYSTEM"},
        {"div_precision_increment": 4},
        {"sql_mode": "STRICT_ALL_TABLES"},
        {"sql_mode": "ONLY_FULL_GROUP_BY"},
    ],
)
def test_mysql_inspection_rejects_unsafe_session_semantics(changes: dict) -> None:
    with pytest.raises(ContractError) as error:
        adapters.MySQLSourceAdapter().inspect(
            fake_mysql_read(session_changes=changes), source()
        )
    assert error.value.code == "UNSAFE_SOURCE_SESSION"


@pytest.mark.parametrize("engine", ["MyISAM", "MEMORY", None])
def test_mysql_inspection_requires_transactional_innodb(engine: str | None) -> None:
    with pytest.raises(ContractError) as error:
        adapters.MySQLSourceAdapter().inspect(fake_mysql_read(engine=engine), source())
    assert error.value.code == "UNSUPPORTED_STORAGE_ENGINE"


@pytest.mark.parametrize("version", ["5.6.51", "5.7.7", "10.11.6-MariaDB"])
def test_mysql_governed_version_does_not_claim_mariadb_or_uncapped_legacy(
    version: str,
) -> None:
    with pytest.raises(ContractError) as error:
        adapters.MySQLSourceAdapter().inspect(
            fake_mysql_read(version=version), source()
        )
    assert error.value.code == "UNSUPPORTED_SOURCE_VERSION"


def test_mysql_datetime_storage_is_explicit_and_timestamp_utc() -> None:
    adapter = adapters.MySQLSourceAdapter()
    with pytest.raises(ContractError) as error:
        adapter.validate_field(field("created_at", "datetime"), "datetime(6)")
    assert error.value.code == "DATETIME_TIMEZONE_REQUIRED"
    adapter.validate_field(
        field("created_at", "datetime", "Asia/Shanghai"), "datetime(6)"
    )
    adapter.validate_field(field("created_at", "datetime", "UTC"), "timestamp(6)")
    with pytest.raises(ContractError) as error:
        adapter.validate_field(
            field("created_at", "datetime", "Asia/Shanghai"), "timestamp"
        )
    assert error.value.code == "TIMESTAMP_TIMEZONE_MISMATCH"
    adapter.validate_field(field(), "tinyint(1)")
    with pytest.raises(ContractError):
        adapter.validate_field(field("flag", "boolean"), "tinyint(1)")


def test_postgres_time_semantics_and_fingerprint_shape_are_unchanged() -> None:
    adapter = adapters.PostgresSourceAdapter()
    adapter.validate_field(field("time", "datetime"), "timestamp(6) with time zone")
    with pytest.raises(ContractError) as error:
        adapter.validate_field(field("time", "datetime"), "timestamp without time zone")
    assert error.value.code == "NAIVE_TIMESTAMP_UNSUPPORTED"
    columns = [{"name": "id", "data_type": "bigint", "nullable": False}]

    def read(sql: str, params: Any) -> pd.DataFrame:
        return pd.DataFrame(
            columns if sql == adapters._PG_COLUMNS_SQL else [{"columns": ["id"]}],
            dtype=object,
        )

    inspected = adapter.inspect(read, source())
    assert inspected.columns == columns
    assert inspected.unique_keys == [["id"]]
    assert inspected.properties is None


def test_mysql_join_key_coercion_cannot_weaken_uniqueness() -> None:
    adapter = adapters.MySQLSourceAdapter()
    left = {"data_type": "bigint(20)", "collation": None, "character_set": None}
    adapter.validate_join_key(left, left, field(), field())
    with pytest.raises(ContractError) as error:
        adapter.validate_join_key(
            left, {**left, "data_type": "double"}, field(), field()
        )
    assert error.value.code == "INCOMPATIBLE_JOIN_KEYS"
    left = {
        "data_type": "varchar(32)",
        "collation": "utf8mb4_bin",
        "character_set": "utf8mb4",
    }
    with pytest.raises(ContractError):
        adapter.validate_join_key(
            left,
            {**left, "collation": "utf8mb4_general_ci"},
            field("code", "string"),
            field("code", "string"),
        )


def test_mysql_metadata_lock_uses_quoted_select_not_lock_tables_privileges() -> None:
    calls = []
    crafted = source().model_copy(update={"table_name": "orders` WHERE TRUE --"})
    adapters.MySQLSourceAdapter().lock(
        lambda sql, parameters: calls.append((sql, parameters)), crafted
    )
    assert calls == [
        ("SELECT `id` FROM `business`.`orders`` WHERE TRUE --` LIMIT 0", None)
    ]


def test_mysql_result_metadata_and_exact_values_include_empty_decimal_columns() -> None:
    frame = pd.DataFrame(
        [
            {"amount": Decimal("9007199254740993.010000"), "id": 18446744073709551615},
            {"amount": None, "id": None},
        ],
        dtype=object,
    )
    frame.attrs.update(dialect="mysql", mysql_types={"amount": 246, "id": 8})
    result = serialize_frame(frame)
    assert result["rows"] == [
        {"amount": "9007199254740993.010000", "id": "18446744073709551615"},
        {"amount": None, "id": None},
    ]
    assert result["column_metadata"][0] == {
        "name": "amount",
        "dialect": "mysql",
        "mysql_type_code": 246,
        "postgres_type_oid": None,
        "json_encoding": "decimal-string",
    }
    empty = frame.iloc[:0].copy()
    assert (
        serialize_frame(empty)["column_metadata"][0]["json_encoding"]
        == "decimal-string"
    )
