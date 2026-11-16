# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Physical source checks for governed business databases, never metadata storage.

PostgreSQL fingerprint v2 includes generated expressions and comparison semantics. MySQL requires InnoDB tables,
complete unique keys, explicit datetime storage semantics and a pinned read-only
session. Only SELECT privileges on the business schema are needed for inspection.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
import re
from typing import Any, Iterator

import pandas as pd
from psycopg import sql as pg_sql

from auto_ontology.connectors.mysql import MySQLDatabase
from auto_ontology.connectors.postgres import PostgresDatabase
from auto_ontology.semantic.contracts.models import (
    ContractError,
    FieldBinding,
    SourceBinding,
)

SourceConnector = PostgresDatabase | MySQLDatabase
SourceReader = Callable[[str, list[Any] | dict[str, Any] | None], pd.DataFrame]


def require(condition: bool, code: str, message: str) -> None:
    if not condition:
        raise ContractError(code, message)


def normalize_dialect(dialect: str | None) -> str | None:
    return "postgres" if dialect == "postgresql" else dialect


_PG_COLUMNS_SQL = """
SELECT a.attname AS name, pg_catalog.format_type(a.atttypid, a.atttypmod) AS data_type,
       NOT a.attnotnull AS nullable, a.atttypid AS type_oid,
       a.attcollation AS collation_oid, co.collname AS collation,
       co.collprovider AS collation_provider, co.collcollate AS collation_locale,
       pg_catalog.pg_collation_actual_version(co.oid) AS collation_version,
       a.attgenerated AS generated,
       CASE WHEN a.attgenerated <> '' THEN pg_catalog.pg_get_expr(ad.adbin, ad.adrelid) END AS generation_expression,
       NOT EXISTS (
           SELECT 1 FROM pg_catalog.pg_depend d
           LEFT JOIN pg_catalog.pg_proc p ON d.refclassid = 'pg_catalog.pg_proc'::regclass AND p.oid = d.refobjid
           LEFT JOIN pg_catalog.pg_operator op ON d.refclassid = 'pg_catalog.pg_operator'::regclass AND op.oid = d.refobjid
           WHERE d.classid = 'pg_catalog.pg_attrdef'::regclass AND d.objid = ad.oid
             AND (p.pronamespace <> 'pg_catalog'::regnamespace OR op.oprnamespace <> 'pg_catalog'::regnamespace)
       ) AS generated_builtin_only
FROM pg_catalog.pg_attribute a
JOIN pg_catalog.pg_class c ON c.oid = a.attrelid
JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
LEFT JOIN pg_catalog.pg_attrdef ad ON ad.adrelid = a.attrelid AND ad.adnum = a.attnum
LEFT JOIN pg_catalog.pg_collation co ON co.oid = a.attcollation
WHERE n.nspname = %(schema)s AND c.relname = %(table)s
  AND a.attnum > 0 AND NOT a.attisdropped
ORDER BY a.attnum
"""
_PG_UNIQUE_SQL = """
SELECT array_agg(a.attname ORDER BY k.ordinality) AS columns
FROM pg_catalog.pg_index i
JOIN pg_catalog.pg_class c ON c.oid = i.indrelid
JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
CROSS JOIN LATERAL unnest(i.indkey) WITH ORDINALITY AS k(attnum, ordinality)
JOIN pg_catalog.pg_attribute a ON a.attrelid = c.oid AND a.attnum = k.attnum
JOIN pg_catalog.pg_opclass oc ON oc.oid = i.indclass[k.ordinality - 1]
JOIN pg_catalog.pg_am am ON am.oid = oc.opcmethod
WHERE n.nspname = %(schema)s AND c.relname = %(table)s
  AND i.indisunique AND i.indisvalid AND i.indpred IS NULL AND i.indexprs IS NULL
  AND k.ordinality <= i.indnkeyatts
GROUP BY i.indexrelid
HAVING bool_and(oc.opcdefault AND oc.opcnamespace = 'pg_catalog'::regnamespace
                AND am.amname = 'btree' AND i.indcollation[k.ordinality - 1] = a.attcollation)
ORDER BY i.indexrelid
"""
_PG_TABLE_SQL = """
SELECT c.relkind AS kind, c.relrowsecurity AS row_security,
       c.relforcerowsecurity AS force_row_security, c.relhassubclass AS has_children,
       EXISTS (SELECT 1 FROM pg_catalog.pg_inherits i WHERE i.inhrelid = c.oid) AS has_parent
FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = %(schema)s AND c.relname = %(table)s
"""
_MYSQL_TABLE_SQL = """
SELECT TABLE_SCHEMA AS schema_name, TABLE_NAME AS table_name,
       TABLE_TYPE AS table_type, ENGINE AS engine, TABLE_COLLATION AS collation
FROM information_schema.TABLES
WHERE TABLE_SCHEMA = %(schema)s AND TABLE_NAME = %(table)s
"""
_MYSQL_COLUMNS_SQL = """
SELECT COLUMN_NAME AS name, COLUMN_TYPE AS data_type,
       IS_NULLABLE AS nullable, CHARACTER_SET_NAME AS character_set,
       COLLATION_NAME AS collation, EXTRA AS extra,
       GENERATION_EXPRESSION AS generation_expression
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = %(schema)s AND TABLE_NAME = %(table)s
ORDER BY ORDINAL_POSITION
"""
_MYSQL_UNIQUE_SQL = """
SELECT INDEX_NAME AS index_name, COLUMN_NAME AS column_name,
       SEQ_IN_INDEX AS key_position, SUB_PART AS prefix_length,
       INDEX_TYPE AS index_type
FROM information_schema.STATISTICS
WHERE TABLE_SCHEMA = %(schema)s AND TABLE_NAME = %(table)s AND NON_UNIQUE = 0
ORDER BY INDEX_NAME, SEQ_IN_INDEX
"""
_MYSQL_SESSION_SQL = """
SELECT DATABASE() AS database_name, @@session.time_zone AS time_zone,
       @@session.div_precision_increment AS div_precision_increment,
       @@session.sql_mode AS sql_mode, VERSION() AS version
"""


@dataclass(frozen=True)
class SourceStructure:
    columns: list[dict[str, Any]]
    unique_keys: list[list[str]]
    properties: dict[str, Any] | None = None


class PostgresSourceAdapter:
    dialect = "postgres"
    connector_class = PostgresDatabase

    def data_type(self, value: str | None) -> str | None:
        value = (value or "").lower()
        if "[]" in value:
            return None
        if value.startswith(
            (
                "numeric",
                "decimal",
                "smallint",
                "integer",
                "bigint",
                "real",
                "double precision",
                "int2",
                "int4",
                "int8",
                "float",
            )
        ):
            return "number"
        if value.startswith(("character", "varchar", "text", "name", "uuid")):
            return "string"
        if value in {"boolean", "bool"}:
            return "boolean"
        if value == "date":
            return "date"
        if value == "timestamptz" or (
            value.startswith("timestamp") and value.endswith(" with time zone")
        ):
            return "datetime"
        return None

    def validate_field(
        self, field: FieldBinding, native_type: str | None, *, physical: bool = False
    ) -> None:
        require(
            field.datetime_timezone is None,
            "UNSUPPORTED_STORAGE_TIMEZONE",
            "PostgreSQL datetime bindings use timestamptz; do not assign a wall-clock storage timezone",
        )
        require(
            not (
                field.data_type == "datetime"
                and (native_type or "").lower().startswith("timestamp")
                and self.data_type(native_type) is None
            ),
            "NAIVE_TIMESTAMP_UNSUPPORTED",
            f"Field {field.field_id}: datetime requires PostgreSQL timestamp with time zone; naive timestamps need explicit source-timezone support",
        )
        require(
            self.data_type(native_type) == field.data_type,
            "SOURCE_CHANGED" if physical else "TYPE_MISMATCH",
            f"Field {field.field_id}: type differs from the {'physical source' if physical else 'Catalog'}",
        )

    def validate_join_key(
        self,
        left: dict[str, Any],
        right: dict[str, Any],
        left_field: FieldBinding,
        right_field: FieldBinding,
    ) -> None:
        # Same native comparison or an exact numeric widening only. In
        # particular, bigint/numeric -> float can collapse distinct unique keys.
        exact = {20, 21, 23, 1700}
        same = left["type_oid"] == right["type_oid"]
        widening = left["type_oid"] in exact and right["type_oid"] in exact
        require(
            (same or widening) and left["collation_oid"] == right["collation_oid"],
            "INCOMPATIBLE_JOIN_KEYS",
            f"Join {left_field.field_id}/{right_field.field_id} requires compatible exact types and identical collation semantics",
        )

    def lock(self, read: SourceReader, source: SourceBinding) -> None:
        statement = (
            pg_sql.SQL("LOCK TABLE {} IN ACCESS SHARE MODE")
            .format(pg_sql.Identifier(source.schema_name, source.table_name))
            .as_string()
        )
        read(statement, None)

    def inspect(self, read: SourceReader, source: SourceBinding) -> SourceStructure:
        parameters = {"schema": source.schema_name, "table": source.table_name}
        columns = read(_PG_COLUMNS_SQL, parameters).to_dict("records")
        unique = [
            list(row["columns"])
            for row in read(_PG_UNIQUE_SQL, parameters).to_dict("records")
        ]
        table = read(_PG_TABLE_SQL, parameters).to_dict("records")
        require(
            len(table) == 1
            and table[0]["kind"] == "r"
            and not table[0]["has_children"]
            and not table[0]["has_parent"],
            "UNSUPPORTED_SOURCE_STRUCTURE",
            "Governed PostgreSQL sources require ordinary tables without inheritance",
        )
        require(
            all(column["generated_builtin_only"] for column in columns),
            "UNSUPPORTED_GENERATED_DEPENDENCY",
            "Generated columns using custom functions or operators require a materialized, reviewed source",
        )
        return SourceStructure(
            columns, unique, {"fingerprint_version": 2, "table": table[0]}
        )


class MySQLSourceAdapter:
    dialect = "mysql"
    connector_class = MySQLDatabase

    def data_type(self, value: str | None) -> str | None:
        match = re.match(r"^([a-z]+)", (value or "").lower())
        base = match.group(1) if match else ""
        if base in {
            "tinyint",
            "smallint",
            "mediumint",
            "int",
            "integer",
            "bigint",
            "decimal",
            "numeric",
            "float",
            "double",
            "real",
        }:
            return "number"
        if base in {
            "char",
            "varchar",
            "tinytext",
            "text",
            "mediumtext",
            "longtext",
            "enum",
        }:
            return "string"
        if base in {"datetime", "timestamp"}:
            return "datetime"
        if base == "date":
            return "date"
        # BOOL is a TINYINT alias without a 0/1 constraint in MySQL 5.7.
        # Binary/BIT, JSON, SET and spatial values need explicit typed support.
        return None

    def validate_field(
        self, field: FieldBinding, native_type: str | None, *, physical: bool = False
    ) -> None:
        require(
            self.data_type(native_type) == field.data_type,
            "SOURCE_CHANGED" if physical else "TYPE_MISMATCH",
            f"Field {field.field_id}: unsupported or mismatched MySQL type; TINYINT flags must be modeled as numbers",
        )
        if field.data_type == "datetime":
            require(
                bool(field.datetime_timezone),
                "DATETIME_TIMEZONE_REQUIRED",
                f"Field {field.field_id}: MySQL datetime requires an explicit storage timezone",
            )
            if (native_type or "").lower().startswith("timestamp"):
                require(
                    field.datetime_timezone in {"UTC", "+00:00"},
                    "TIMESTAMP_TIMEZONE_MISMATCH",
                    f"Field {field.field_id}: MySQL TIMESTAMP is read in UTC; declare datetime_timezone='UTC'",
                )
        else:
            require(
                field.datetime_timezone is None,
                "INVALID_STORAGE_TIMEZONE",
                "Storage timezone applies only to datetime fields",
            )

    def validate_join_key(
        self,
        left: dict[str, Any],
        right: dict[str, Any],
        left_field: FieldBinding,
        right_field: FieldBinding,
    ) -> None:
        # A target's UNIQUE index uses its own native comparison semantics.
        # MySQL's implicit numeric/collation coercion can otherwise make two
        # distinct target keys compare equal to one source key and cause fanout.
        compatible = str(left["data_type"]).lower() == str(right["data_type"]).lower()
        if left_field.data_type == "string":
            compatible = compatible and all(
                left.get(key) == right.get(key)
                for key in ("character_set", "collation")
            )
        if left_field.data_type == "datetime":
            compatible = (
                compatible
                and left_field.datetime_timezone == right_field.datetime_timezone
            )
        require(
            compatible,
            "INCOMPATIBLE_JOIN_KEYS",
            "MySQL relationship keys require matching physical types, charset/collation and datetime storage timezone; implicit coercion cannot prove safe cardinality",
        )

    def lock(self, read: SourceReader, source: SourceBinding) -> None:
        def quoted(identifier: str) -> str:
            require(
                bool(identifier) and "\x00" not in identifier,
                "INVALID_IDENTIFIER",
                "MySQL identifiers must be nonempty and contain no NUL",
            )
            return "`" + identifier.replace("`", "``") + "`"

        require(
            bool(source.fields),
            "INVALID_SOURCE",
            "A governed source requires registered columns",
        )
        columns = ", ".join(quoted(field.column_name) for field in source.fields)
        # A normal SELECT acquires transaction-duration metadata locks without
        # requiring LOCK TABLES. It also verifies privileges on the bound columns.
        read(
            f"SELECT {columns} FROM {quoted(source.schema_name)}.{quoted(source.table_name)} LIMIT 0",
            None,
        )

    def inspect(self, read: SourceReader, source: SourceBinding) -> SourceStructure:
        parameters = {"schema": source.schema_name, "table": source.table_name}
        tables = read(_MYSQL_TABLE_SQL, parameters).to_dict("records")
        require(
            len(tables) == 1,
            "SOURCE_CHANGED",
            f"Source {source.source_id}: physical MySQL table is unavailable",
        )
        table = tables[0]
        require(
            table["schema_name"] == source.schema_name
            and table["table_name"] == source.table_name,
            "BINDING_MISMATCH",
            "MySQL schema/table names do not match the physical source",
        )
        require(
            table["table_type"] == "BASE TABLE"
            and (table["engine"] or "").lower() == "innodb",
            "UNSUPPORTED_STORAGE_ENGINE",
            "Governed MySQL sources require InnoDB base tables; views and nontransactional engines are unsupported",
        )
        session = read(_MYSQL_SESSION_SQL, None).to_dict("records")[0]
        require(
            session["database_name"] == source.schema_name,
            "BINDING_MISMATCH",
            "MySQL sources must use the configured connection database",
        )
        require(
            session["time_zone"] == "+00:00",
            "UNSAFE_SOURCE_SESSION",
            "Governed MySQL sessions must use UTC",
        )
        require(
            session["div_precision_increment"] == 30,
            "UNSAFE_SOURCE_SESSION",
            "Governed MySQL sessions require div_precision_increment=30",
        )
        modes = set(str(session["sql_mode"]).upper().split(","))
        require(
            ("STRICT_ALL_TABLES" in modes or "STRICT_TRANS_TABLES" in modes)
            and "ONLY_FULL_GROUP_BY" in modes,
            "UNSAFE_SOURCE_SESSION",
            "Governed MySQL sessions require strict SQL mode and ONLY_FULL_GROUP_BY",
        )
        version_match = re.match(r"^(\d+)\.(\d+)\.(\d+)", str(session["version"]))
        require(
            version_match is not None
            and "mariadb" not in str(session["version"]).lower()
            and tuple(map(int, version_match.groups())) >= (5, 7, 8),
            "UNSUPPORTED_SOURCE_VERSION",
            "Governed MySQL requires MySQL 5.7.8 or newer; MariaDB is not a validated governed dialect",
        )
        columns = read(_MYSQL_COLUMNS_SQL, parameters).to_dict("records")
        for column in columns:
            column["nullable"] = column["nullable"] == "YES"
        indexes: dict[str, list[dict[str, Any]]] = {}
        for row in read(_MYSQL_UNIQUE_SQL, parameters).to_dict("records"):
            indexes.setdefault(row["index_name"], []).append(row)
        unique = []
        for parts in indexes.values():
            if any(
                part["prefix_length"] is not None or part["column_name"] is None
                for part in parts
            ):
                continue
            parts.sort(key=lambda part: part["key_position"])
            if [part["key_position"] for part in parts] == list(
                range(1, len(parts) + 1)
            ):
                unique.append([part["column_name"] for part in parts])
        # Numeric and timezone execution rules are part of the reviewed physical
        # signature. Server patch version is diagnostic, not semantic identity.
        properties = {
            "table": table,
            "time_zone": session["time_zone"],
            "div_precision_increment": session["div_precision_increment"],
            "sql_mode": sorted(modes),
        }
        return SourceStructure(columns, unique, properties)


SourceAdapter = PostgresSourceAdapter | MySQLSourceAdapter
_ADAPTERS: dict[str, SourceAdapter] = {
    "postgres": PostgresSourceAdapter(),
    "mysql": MySQLSourceAdapter(),
}


def adapter_for(dialect: str) -> SourceAdapter:
    normalized = normalize_dialect(dialect)
    require(
        normalized in _ADAPTERS,
        "UNSUPPORTED_DIALECT",
        "Governed queries support PostgreSQL and MySQL business sources",
    )
    return _ADAPTERS[normalized]


def connector_adapter(connector: Any) -> SourceAdapter:
    for adapter in _ADAPTERS.values():
        if (
            isinstance(connector, adapter.connector_class)
            and normalize_dialect(connector.dialect) == adapter.dialect
        ):
            return adapter
    raise ContractError(
        "CONNECTION_UNAVAILABLE",
        "A real PostgreSQL or MySQL connector must match the Catalog database",
    )


@contextmanager
def source_transaction(
    connector: SourceConnector, *, timeout_s: float = 30
) -> Iterator[SourceReader]:
    """Use a single pinned source session; HTTP/Chat own driver error cleaning."""
    with connector.read_transaction(timeout_s=timeout_s) as read:
        yield read
