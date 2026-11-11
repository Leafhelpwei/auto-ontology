# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Query identity must not erase case-sensitive business values."""

from __future__ import annotations

import pytest

from auto_ontology.dal.sql_attributes import _expression_key


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("SELECT 'PAID'", "SELECT 'paid'"),
        ("SELECT 'a  b'", "SELECT 'a b'"),
        ('SELECT "Amount" FROM orders', 'SELECT "amount" FROM orders'),
        ("SELECT amount FROM Orders", "SELECT amount FROM orders"),
        ("SELECT amount FROM Date", "SELECT amount FROM date"),
        ("SELECT N'PAID'", "SELECT N'paid'"),
        ("SELECT 1; SELECT 'A'", "SELECT 1; SELECT 'a'"),
        ("SELECT NVL(x, 1) FROM t", "SELECT COALESCE(x, 1) FROM t"),
        ("SELECT x FROM t /*! WHERE 1 = 0 */", "SELECT x FROM t"),
    ],
)
def test_different_scope_tokens_never_compare_equal(left: str, right: str) -> None:
    assert _expression_key(left) != _expression_key(right)


def test_keyword_case_and_external_whitespace_are_ignored() -> None:
    assert _expression_key("SELECT  sum(amount)\n FROM p") == _expression_key(
        "select sum(amount) from p"
    )


def test_unparseable_sql_only_compares_exact_text() -> None:
    assert _expression_key("SELECT ((( 'A'") != _expression_key("SELECT ((( 'a'")
