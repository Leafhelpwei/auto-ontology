# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Source errors remain actionable without leaking connection/SQL details."""

import json
from unittest.mock import Mock

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
import mysql.connector
import psycopg
import pytest

from auto_ontology.server.chat import metrics_flow
from auto_ontology.server.metrics.router import MetricsRoute


@pytest.mark.parametrize("error_type", [mysql.connector.Error, psycopg.Error])
def test_http_source_errors_are_sanitized(error_type: type[Exception]) -> None:
    app = FastAPI()
    router = APIRouter(route_class=MetricsRoute)

    @router.get("/probe")
    def probe() -> dict:
        raise error_type("private-password private-table private-host")

    app.include_router(router)
    with TestClient(app) as client:
        response = client.get("/probe")
    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "SOURCE_QUERY_FAILED"
    assert "private-" not in response.text


@pytest.mark.parametrize("error_type", [mysql.connector.Error, psycopg.Error])
def test_chat_source_errors_are_untrusted_and_sanitized(
    monkeypatch: pytest.MonkeyPatch, error_type: type[Exception]
) -> None:
    monkeypatch.setattr(metrics_flow, "require_metric_access", lambda *a, **k: None)
    monkeypatch.setattr(
        metrics_flow.service,
        "executable_model",
        Mock(side_effect=error_type("private-password private-table private-host")),
    )
    interpreter = Mock()
    execute = Mock()
    monkeypatch.setattr(metrics_flow, "interpret_question", interpreter)
    monkeypatch.setattr(metrics_flow.service, "execute", execute)
    events = list(
        metrics_flow.stream_metric_response(
            {
                "question": "订单金额",
                "metric_context": {"model_id": "orders"},
                "user_id": "u",
                "conversation_history": [],
            }
        )
    )
    context = events[-1]["answer"]["query_context"]
    assert context["trusted"] is False
    assert context["clarification"]["code"] == "SOURCE_QUERY_FAILED"
    assert "private-" not in json.dumps(events)
    interpreter.assert_not_called()
    execute.assert_not_called()
