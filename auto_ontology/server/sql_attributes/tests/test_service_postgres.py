# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Real catalog transactions for SQLAttribute certification and source links."""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from unittest.mock import Mock

import pytest
from sqlalchemy import select

from auto_ontology.dal import schema as s
from auto_ontology.dal.session import store
from auto_ontology.server.sql_attributes import service


@pytest.fixture
def catalog_attribute(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, str]]:
    if not os.environ.get("POSTGRES_USER"):
        pytest.skip("POSTGRES_* not set")
    prefix = "history-cert-" + uuid.uuid4().hex
    term_ids = [str(uuid.uuid4()), str(uuid.uuid4())]
    attr_id = str(uuid.uuid4())
    query_id = str(uuid.uuid4())
    original_sql = f"SELECT 1 /* {prefix} */"
    for index, term_id in enumerate(term_ids):
        store().query_write(
            s.term.insert().values(id=term_id, name=f"{prefix}-{index}")
        )
    store().query_write(
        s.sql_attribute.insert().values(
            id=attr_id,
            name=prefix,
            description="Original definition",
            expression=original_sql,
            source="manual",
            certified=True,
        )
    )
    store().query_write(
        s.sql_query.insert().values(id=query_id, sql_full_query=original_sql)
    )
    store().query_write(
        s.sql_attribute__term.insert().values(attribute_id=attr_id, term_id=term_ids[0])
    )
    store().query_write(
        s.sql_attribute__sql.insert().values(
            attribute_id=attr_id, sql_query_id=query_id
        )
    )
    monkeypatch.setattr(service, "_resolve_database_name", lambda _: None)
    monkeypatch.setattr(service, "get_dialects", lambda _: ["postgres"])
    monkeypatch.setattr(service, "get_schemas", lambda _: {})
    monkeypatch.setattr(service, "validate_sql", Mock(return_value=object()))
    monkeypatch.setattr(service, "_reembed_sql_attribute", Mock())
    monkeypatch.setattr(service, "realign_sql_attribute_pii", Mock())
    monkeypatch.setattr(service, "get_tag_by_name", lambda _: None)
    try:
        yield {
            "id": attr_id,
            "name": prefix,
            "old_term": term_ids[0],
            "new_term": term_ids[1],
            "query_id": query_id,
            "expression": original_sql,
        }
    finally:
        store().query_write(
            s.sql_attribute.delete().where(s.sql_attribute.c.id == attr_id)
        )
        store().query_write(s.term.delete().where(s.term.c.id.in_(term_ids)))
        store().query_write(s.sql_query.delete().where(s.sql_query.c.id == query_id))


def test_failed_replacement_rolls_back_expression_certification_and_edges(
    catalog_attribute: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    attr = catalog_attribute
    monkeypatch.setattr(
        service,
        "_persist_attr_with_sql",
        Mock(side_effect=RuntimeError("source write failed")),
    )
    with pytest.raises(RuntimeError, match="source write failed"):
        service.update_sql_attribute(
            attr_id=attr["id"],
            name=attr["name"],
            expression="SELECT 2",
            term_id=attr["new_term"],
        )
    row = service.get_full_sql_attribute_by_id(attr["id"])
    assert row["expression"] == attr["expression"]
    assert row["certified"] is True
    assert row["term_id"] == attr["old_term"]
    links = store().query_read(
        select(s.sql_attribute__sql.c.sql_query_id).where(
            s.sql_attribute__sql.c.attribute_id == attr["id"]
        )
    )
    assert links == [{"sql_query_id": attr["query_id"]}]
    service._reembed_sql_attribute.assert_not_called()


def test_term_reassignment_clears_persisted_certification(
    catalog_attribute: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    attr = catalog_attribute

    def relink_source(*_args: object) -> None:
        store().query_write(
            s.sql_attribute__sql.insert().values(
                attribute_id=attr["id"], sql_query_id=attr["query_id"]
            )
        )

    monkeypatch.setattr(service, "_persist_attr_with_sql", relink_source)
    service.update_sql_attribute(
        attr_id=attr["id"],
        name=attr["name"],
        expression=attr["expression"],
        term_id=attr["new_term"],
    )
    row = service.get_full_sql_attribute_by_id(attr["id"])
    assert row["certified"] is False
    assert row["term_id"] == attr["new_term"]
