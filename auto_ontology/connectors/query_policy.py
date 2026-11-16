# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Deterministic policy for untrusted, interactive SQL (never ingestion SQL)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterable

import pandas as pd
import sqlglot
from sqlglot import exp


class UnsafeQuery(ValueError):
    """A query is outside the interactive read-only SQL policy."""


class ResultLimitExceeded(ValueError):
    """A result cannot be safely represented within the interactive budget."""


# Explicit, reviewed functions. Unknown functions, including UDFs, must not
# inherit permission merely because they appear inside SELECT. sqlglot's names
# are normalized (e.g. DATE_FORMAT becomes TIME_TO_STR). Neither this list nor
# READ ONLY replaces SELECT-only credentials and restricted routine privileges.
_FUNCTIONS = frozenset(
    """
ABS ACOS ACOSH ADD_MONTHS AND ANY_VALUE APPROX_DISTINCT APPROX_QUANTILE
ARRAY ARRAY_AGG ARRAY_CONCAT ARRAY_CONTAINS ARRAY_LENGTH ARRAY_SIZE ASCII ASIN
ASINH ATAN ATAN2 ATANH AVG BITWISE_AND_AGG BITWISE_OR_AGG BOOL_AND BOOL_OR
CASE EXISTS CAST TRY_CAST CEIL CHR COALESCE CONCAT CONCAT_WS CONVERT CONVERT_TIMEZONE COS
COSH COT COUNT COUNT_IF CUME_DIST CURRENT_DATE CURRENT_DATETIME CURRENT_TIME
CURRENT_TIMESTAMP CURRENT_SCHEMA CURRENT_DATABASE NOW JULIANDAY STRFTIME DATE DATE_ADD DATE_DIFF DATE_FROM_PARTS DATE_SUB DATE_TRUNC
DATE_TO_DI DATE_TO_STR DATETIME DATETIME_ADD DATETIME_DIFF DATETIME_SUB
DATETIME_TRUNC DAY DAY_OF_MONTH DAY_OF_WEEK DAY_OF_YEAR DECODE DENSE_RANK
DI_TO_DATE DIV DOW DOY ENCODE ENDS_WITH EXP EXTRACT FILTER FIRST FIRST_VALUE
FLOOR FORMAT FORMAT_NUMBER FROM_BASE FROM_UNIXTIME GENERATE_SERIES GREATEST
GROUP_CONCAT HEX HOUR IF IFNULL INITCAP INSTR INT_DIV IS_INF IS_NAN JSON_ARRAY
JSON_ARRAY_AGG JSON_ARRAY_LENGTH JSON_EXTRACT JSON_EXTRACT_SCALAR JSON_OBJECT
JSON_OBJECT_AGG JSON_TYPE JSONB_EXTRACT JSONB_EXTRACT_SCALAR LAG LAST
LAST_DAY LAST_VALUE LEAD LEAST LEFT LENGTH LN LOG LOG2 LOG10 LOWER LPAD
LTRIM MAX MD5 MEDIAN MIN MINUTE MOD MONTH MONTHS_BETWEEN NTH_VALUE NTILE
NULLIF OCTET_LENGTH OR PARSE_DATE PARSE_DATETIME PARSE_TIMESTAMP PERCENT_RANK
PERCENTILE_CONT PERCENTILE_DISC POSITION POW QUANTILE QUARTER RANK REGEXP_COUNT
REGEXP_EXTRACT REGEXP_EXTRACT_ALL REGEXP_INSTR REGEXP_LIKE REGEXP_REPLACE
REGEXP_SPLIT REGR_AVGX REGR_AVGY REGR_COUNT REGR_INTERCEPT REGR_R2 REGR_SLOPE
REGR_SXX REGR_SXY REGR_SYY REPEAT REPLACE REVERSE RIGHT ROUND ROW_NUMBER
RPAD RTRIM SAFE_DIVIDE SECOND SHA SHA2 SIGN SIN SINH SPACE SPLIT SPLIT_PART
SQRT STARTS_WITH STDDEV STDDEV_POP STDDEV_SAMP STR_POSITION STR_TO_DATE
STR_TO_TIME STR_TO_UNIX STRING_AGG SUBSTRING SUM TAN TANH TIME TIME_ADD
TIME_DIFF TIME_SUB TIME_TO_STR TIMESTAMP TIMESTAMP_ADD TIMESTAMP_DIFF
TIMESTAMP_SUB TIMESTAMP_TRUNC TIME_STR_TO_DATE TIME_STR_TO_TIME TIME_TO_UNIX
TO_CHAR TO_DAYS TO_JSON TO_NUMBER TRANSLATE TRIM TRUNC TS_OR_DS_ADD
TS_OR_DS_DIFF TS_OR_DS_TO_DATE TS_OR_DS_TO_DATETIME TS_OR_DS_TO_TIME
TS_OR_DS_TO_TIMESTAMP TS_OR_DS_TO_TIMESTAMP_NTZ TS_OR_DS_TO_TIME_STR
UNHEX UNIX_DATE UNIX_MICROS UNIX_MILLIS UNIX_SECONDS UNIX_TIMESTAMP
UNIX_TO_STR UNIX_TO_TIME UPPER VAR_POP VAR_SAMP VARIANCE WEEK WEEK_OF_YEAR
YEAR YEAR_OF_WEEK
""".split()
)


def validate_read_query(sql: str, dialect: str | None = None) -> exp.Query:
    """Accept one read query; reject mutations at every nesting depth.

    SQL is parsed in the selected engine's dialect. Re-rendering the checked
    tree before execution also removes comments, including executable hints.
    """
    if not isinstance(sql, str) or not sql.strip() or len(sql) > 128_000:
        raise UnsafeQuery(
            "A single read-only query of at most 128000 characters is required."
        )
    # MySQL/MariaDB execute these comments; ordinary SQL parsers ignore them.
    try:
        if ("/*!" in sql or "/*M!" in sql.upper()) and any(
            comment.lstrip().upper().startswith(("!", "M!"))
            for token in sqlglot.tokenize(sql, read=dialect or None)
            for comment in token.comments
        ):
            raise UnsafeQuery("Executable SQL comments are not allowed.")
        statements = sqlglot.parse(sql, read=dialect or None)
    except (sqlglot.errors.SqlglotError, ValueError, RecursionError) as exc:
        raise UnsafeQuery("The query could not be verified as read-only.") from exc
    if len(statements) != 1 or not isinstance(
        statements[0], (exp.Select, exp.SetOperation)
    ):
        raise UnsafeQuery("Only one SELECT query is allowed.")
    tree = statements[0]
    for node in tree.walk():
        if isinstance(
            node,
            (
                exp.DML,
                exp.DDL,
                exp.Command,
                exp.Into,
                exp.Lock,
                exp.PropertyEQ,
                exp.SessionParameter,
                exp.Parameter,
            ),
        ):
            raise UnsafeQuery(
                "Writes, session variables, locking reads and SELECT INTO are not allowed."
            )
        if isinstance(node, exp.Table) and not isinstance(node.this, exp.Identifier):
            raise UnsafeQuery(
                "Table functions and external file sources are not allowed."
            )
        if (
            isinstance(node, exp.Table)
            and dialect == "duckdb"
            and any(character in node.name for character in ("/", "\\", ".", "*", "?"))
        ):
            # Quoted path-like names are implicit file scans in DuckDB.
            raise UnsafeQuery("External file sources are not allowed.")
        if isinstance(node, exp.Func):
            name = (
                node.name.upper()
                if isinstance(node, exp.Anonymous)
                else node.sql_name()
            )
            if name not in _FUNCTIONS:
                raise UnsafeQuery(
                    "The query calls a function outside the read-only allowlist."
                )
            if isinstance(node.parent, exp.Dot):
                # A qualified routine can shadow a built-in with arbitrary code.
                raise UnsafeQuery("Qualified routine calls are not allowed.")
        if isinstance(node, exp.Hint):
            raise UnsafeQuery("Query hints are not allowed in interactive SQL.")
    return tree


def bounded_query(tree: exp.Query, dialect: str, max_rows: int) -> str:
    """Cap the outer result without changing nested limits or query ordering."""
    query = tree.copy()
    limit = query.args.get("limit")
    if limit is not None:
        value = limit.expression
        if (
            not isinstance(value, exp.Literal)
            or not value.is_int
            or int(value.this) < 0
        ):
            raise UnsafeQuery("Only a nonnegative constant result limit is supported.")
        if int(value.this) <= max_rows:
            return query.sql(dialect=dialect or None, comments=False)
    return query.limit(max_rows).sql(dialect=dialect or None, comments=False)


@dataclass(frozen=True)
class QueryBudget:
    max_rows: int = 1000
    max_bytes: int = 512 * 1024
    max_row_bytes: int = 64 * 1024
    max_columns: int = 128

    def __post_init__(self) -> None:
        if (
            not 1 <= self.max_rows <= 10_000
            or not 1024 <= self.max_bytes <= 1024 * 1024
        ):
            raise ValueError("Invalid interactive result budget")
        if (
            not 1024 <= self.max_row_bytes <= self.max_bytes
            or not 1 <= self.max_columns <= 128
        ):
            raise ValueError("Invalid interactive row/column budget")


@dataclass
class ResultCollector:
    """Bound retained rows and their JSON size before building a DataFrame."""

    columns: list[str]
    budget: QueryBudget
    rows: list[tuple[Any, ...]] = field(default_factory=list)
    returned_bytes: int = 2
    reason: str | None = None

    def __post_init__(self) -> None:
        header_bytes = len(json.dumps(self.columns, ensure_ascii=True).encode())
        if len(self.columns) > self.budget.max_columns or header_bytes > 16 * 1024:
            raise ResultLimitExceeded(
                "The result has too many or excessively long column names."
            )

    def add(self, row: Iterable[Any]) -> bool:
        if len(self.rows) >= self.budget.max_rows:
            self.reason = "row_limit"
            return False
        values = tuple(row)
        # Include column names for every record, as the downstream JSON does.
        # ASCII escaping conservatively budgets the browser/history encoding.
        size = (
            len(
                json.dumps(
                    list(zip(self.columns, values)),
                    ensure_ascii=True,
                    default=str,
                    allow_nan=False,
                ).encode()
            )
            + 1
        )
        if size > self.budget.max_row_bytes:
            raise ResultLimitExceeded(
                "A result row exceeds the interactive byte limit; select fewer or shorter fields."
            )
        if self.returned_bytes + size > self.budget.max_bytes:
            self.reason = "byte_limit"
            return False
        self.returned_bytes += size
        self.rows.append(values)
        return True

    def frame(self) -> pd.DataFrame:
        frame = pd.DataFrame(self.rows, columns=self.columns, dtype=object)
        frame.attrs["query_result"] = {
            "truncated": self.reason is not None,
            "reason": self.reason,
            "returned_rows": len(self.rows),
            "returned_bytes": self.returned_bytes,
            "row_limit": self.budget.max_rows,
            "byte_limit": self.budget.max_bytes,
        }
        return frame


def collect_frame(frame: pd.DataFrame, budget: QueryBudget) -> pd.DataFrame:
    """Bound serialization for legacy warehouse drivers (not a streaming claim)."""
    collector = ResultCollector([str(column) for column in frame.columns], budget)
    for row in frame.itertuples(index=False, name=None):
        if not collector.add(row):
            break
    result = collector.frame()
    result.attrs = {**frame.attrs, **result.attrs}
    return result
