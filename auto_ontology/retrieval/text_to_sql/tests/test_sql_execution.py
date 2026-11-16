# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json
from types import SimpleNamespace

import pandas as pd
import pytest

from auto_ontology.retrieval.text_to_sql.agents import sql_execution
from auto_ontology.retrieval.text_to_sql.agents.response import ResponseAgent
from auto_ontology.connectors.query_policy import QueryBudget, ResultCollector


def test_duplicate_result_columns_are_deduplicated_before_serialization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frame = pd.DataFrame([[1, 2, 3]], columns=["ID", "ID", "ID__2"])
    monkeypatch.setattr(
        sql_execution, "execute_chat_sql", lambda _connector, _sql: frame
    )

    response = sql_execution._run_sql(
        "SELECT first_id, second_id, third_id",
        SimpleNamespace(dialect="postgres"),
        record_numeric_kinds=True,
    )

    assert response.error is None
    assert json.loads(response.result[0]) == [{"ID": 1, "ID__3": 2, "ID__2": 3}]
    assert response.numeric_kinds == [None, None, None]
    assert response.values_digest is not None
    assert list(frame.columns) == ["ID", "ID", "ID__2"]


def test_unique_result_columns_remain_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frame = pd.DataFrame([[1, "Ada"]], columns=["ID", "Name"])
    monkeypatch.setattr(
        sql_execution, "execute_chat_sql", lambda _connector, _sql: frame
    )

    response = sql_execution._run_sql(
        "SELECT id, name",
        SimpleNamespace(dialect="postgres"),
    )

    assert response.error is None
    assert json.loads(response.result[0]) == [{"ID": 1, "Name": "Ada"}]


def test_metadata_errors_are_returned_as_query_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frame = pd.DataFrame([[1]], columns=["ID"])
    monkeypatch.setattr(
        sql_execution, "execute_chat_sql", lambda _connector, _sql: frame
    )
    monkeypatch.setattr(
        sql_execution,
        "numeric_column_kinds",
        lambda _frame: (_ for _ in ()).throw(ValueError("metadata failed")),
    )

    response = sql_execution._run_sql(
        "SELECT id",
        SimpleNamespace(dialect="postgres"),
        record_numeric_kinds=True,
    )

    assert response.error == "metadata failed"
    assert response.result is None


def test_truncation_survives_execution_and_is_disclosed_in_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    collector = ResultCollector(["id"], QueryBudget(max_rows=1))
    assert collector.add([1])
    assert not collector.add([2])
    monkeypatch.setattr(
        sql_execution, "execute_chat_sql", lambda *_args: collector.frame()
    )
    connector = SimpleNamespace(dialect="postgres")
    result = sql_execution._run_sql("SELECT id FROM data", connector)
    assert result.sliced and result.result_metadata["returned_rows"] == 1
    monkeypatch.setattr(
        sql_execution, "resolve_connector_from_tables", lambda *_args: connector
    )
    state = {
        "connectors": [connector],
        "messages": [],
        "path_state": {
            "sql_code": "SELECT id FROM data",
            "sql_generation_result": SimpleNamespace(
                sql_code="SELECT id FROM data", response="Selected IDs."
            ),
        },
    }
    update = sql_execution.SQLExecutionAgent().execute(state)
    answer = ResponseAgent().execute({**state, "path_state": update["path_state"]})[
        "path_state"
    ]["final_response"]
    assert answer["result_metadata"]["truncated"]
    assert (
        "Partial result" in answer["response"]
        and "not the complete result" in answer["response"]
    )
