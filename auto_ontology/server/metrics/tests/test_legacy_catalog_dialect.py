# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Legacy dialects need verified connectors; a model cannot relabel a source."""

from types import SimpleNamespace

import pytest

from auto_ontology.dal import schema as s
from auto_ontology.dal.session import store
from auto_ontology.semantic.contracts.models import ContractError, MetricQueryRequest
from auto_ontology.server.metrics import service
from auto_ontology.server.metrics.tests.test_governance import (
    catalog as catalog,
    publish,
)


def _set_dialect(model, dialect: str | None) -> None:
    store().query_write(
        s.catalog_database.update()
        .where(s.catalog_database.c.id == model.sources[0].datasource_id)
        .values(dialect=dialect)
    )


def test_legacy_catalog_can_publish_and_execute_with_real_postgres(catalog) -> None:
    model, _, _, _ = catalog
    _set_dialect(model, None)
    try:
        publish(model)
        query = MetricQueryRequest(metric_ids=("amount",), all_time=True)
        assert service.execute(model.model_id, query)["rows"] == [{"amount": "350"}]
        # A later corrected ingest must not invalidate an otherwise identical
        # certificate solely because it filled the proven dialect in metadata.
        _set_dialect(model, "postgres")
        assert service.execute(model.model_id, query)["rows"] == [{"amount": "350"}]
    finally:
        _set_dialect(model, "postgres")


@pytest.mark.parametrize("mode", ["missing", "unverified", "ambiguous"])
def test_legacy_catalog_rejects_unproven_connection(
    catalog, monkeypatch, mode: str
) -> None:
    model, connector, _, _ = catalog
    _set_dialect(model, None)
    fake = SimpleNamespace(database_name=connector.database_name, dialect="postgres")
    connections = {
        "missing": [],
        "unverified": [fake],
        "ambiguous": [connector, fake],
    }
    monkeypatch.setattr(service, "get_connectors", lambda: connections[mode])
    try:
        with pytest.raises(ContractError) as rejected:
            service.create(model, user_id="reviewer", owner_id=None)
        assert rejected.value.code == "CONNECTION_UNAVAILABLE"
    finally:
        _set_dialect(model, "postgres")


def test_postgres_model_cannot_reinterpret_a_mysql_catalog(catalog) -> None:
    model, _, _, _ = catalog
    _set_dialect(model, "mysql")
    try:
        with pytest.raises(ContractError) as rejected:
            service.create(model, user_id="reviewer", owner_id=None)
        assert rejected.value.code == "DIALECT_MISMATCH"
    finally:
        _set_dialect(model, "postgres")
