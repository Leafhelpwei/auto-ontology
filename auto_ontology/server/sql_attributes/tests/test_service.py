# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""SQL edits must invalidate certification in the same transaction as the edit."""

from __future__ import annotations

from contextlib import contextmanager
from collections.abc import Iterator
from unittest.mock import Mock

import pytest

from auto_ontology.server.sql_attributes import service


@pytest.fixture
def writes(monkeypatch: pytest.MonkeyPatch) -> dict:
    row = {
        "id": "attr-1",
        "name": "Paid Amount",
        "description": "Sum of paid amounts",
        "expression": "SELECT SUM(amount) FROM orders WHERE status = 'paid'",
        "term_id": "term-1",
        "database_name": "test",
        "certified": True,
    }
    events: list[str] = []

    @contextmanager
    def transaction() -> Iterator[None]:
        events.append("begin")
        try:
            yield
        except Exception:
            events.append("rollback")
            raise
        else:
            events.append("commit")

    def update(_id: str, **kwargs) -> None:
        assert events[-1] == "begin", "update must be in the transaction"
        row.update({k: v for k, v in kwargs.items() if v is not None})

    updater = Mock(side_effect=update)
    monkeypatch.setattr(service, "write_transaction", transaction)
    monkeypatch.setattr(service, "get_full_sql_attribute_by_id", lambda _: dict(row))
    monkeypatch.setattr(service, "get_slim_term_by_id", lambda _: {"name": "Orders"})
    monkeypatch.setattr(service, "find_attr_by_name", lambda *args, **kwargs: None)
    monkeypatch.setattr(service, "_resolve_database_name", lambda _: "test")
    monkeypatch.setattr(service, "get_dialects", lambda _: ["postgres"])
    monkeypatch.setattr(service, "get_schemas", lambda _: {})
    monkeypatch.setattr(service, "validate_sql", Mock(return_value=object()))
    monkeypatch.setattr(service, "detach_existing_sql_edges", Mock())
    monkeypatch.setattr(service, "clear_sql_attribute_description_suggestion", Mock())
    monkeypatch.setattr(service.sql_attr_dal, "update_sql_attribute", updater)
    monkeypatch.setattr(service, "_persist_attr_with_sql", Mock())
    monkeypatch.setattr(service, "link_to_term", Mock())
    monkeypatch.setattr(service, "get_tag_by_name", lambda _: None)
    monkeypatch.setattr(service, "realign_sql_attribute_pii", Mock())
    monkeypatch.setattr(
        service,
        "_reembed_sql_attribute",
        Mock(side_effect=lambda *_: events.append("embed")),
    )
    return {"row": row, "update": updater, "events": events}


@pytest.mark.parametrize("change", ["expression", "term", "description"])
def test_semantic_replacement_clears_certification(writes: dict, change: str) -> None:
    original = dict(writes["row"])
    result = service.update_sql_attribute(
        attr_id="attr-1",
        name=original["name"],
        description="A different population"
        if change == "description"
        else original["description"],
        expression="SELECT SUM(amount) FROM orders"
        if change == "expression"
        else original["expression"],
        term_id="term-2" if change == "term" else "term-1",
    )
    assert result["certified"] is False
    assert writes["update"].call_args.kwargs["certified"] is False
    assert writes["events"] == ["begin", "commit", "embed"]


def test_unchanged_sql_keeps_certification(writes: dict) -> None:
    result = service.update_sql_attribute(
        attr_id="attr-1",
        name=writes["row"]["name"],
        expression=writes["row"]["expression"],
        term_id="term-1",
    )
    assert result["certified"] is True
    assert writes["update"].call_args.kwargs["certified"] is None


def test_failed_sql_link_does_not_commit_or_embed(writes: dict) -> None:
    service._persist_attr_with_sql.side_effect = RuntimeError("write failed")
    with pytest.raises(RuntimeError, match="write failed"):
        service.update_sql_attribute(
            attr_id="attr-1",
            name=writes["row"]["name"],
            expression="SELECT SUM(amount) FROM orders",
            term_id="term-1",
        )
    assert writes["events"] == ["begin", "rollback"]


def test_metadata_meaning_change_requires_recertification(writes: dict) -> None:
    result = service.update_sql_attribute(
        attr_id="attr-1", description="Cancelled order amounts"
    )
    assert result["certified"] is False


def test_cannot_certify_a_changed_definition_in_the_same_patch(writes: dict) -> None:
    with pytest.raises(ValueError, match="certif"):
        service.update_sql_attribute(
            attr_id="attr-1", description="Different scope", certified=True
        )
    writes["update"].assert_not_called()


def test_certification_only_patch_does_not_reembed(writes: dict) -> None:
    result = service.update_sql_attribute(attr_id="attr-1", certified=False)
    assert result["certified"] is False
    service._reembed_sql_attribute.assert_not_called()
