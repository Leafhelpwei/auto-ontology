# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Real PostgreSQL lifecycle, snapshot isolation, source drift and bound queries."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select

from auto_ontology.connectors.postgres import PostgresDatabase
from auto_ontology.dal import governed_models as dal
from auto_ontology.dal import schema as s
from auto_ontology.dal.session import store
from auto_ontology.semantic.contracts.models import (
    ContractError,
    FieldBinding,
    FieldExpression,
    FieldRef,
    GovernedModel,
    Metric,
    MetricQueryRequest,
    Predicate,
    SourceBinding,
)
from auto_ontology.server.metrics import router as api
from auto_ontology.server.metrics import service


@pytest.fixture
def catalog(monkeypatch: pytest.MonkeyPatch):
    dsn = os.environ.get("AUTO_ONTOLOGY_GOVERNED_TEST_DSN")
    if not dsn:
        pytest.skip(
            "Set AUTO_ONTOLOGY_GOVERNED_TEST_DSN to a migrated isolated PostgreSQL database"
        )
    connector = PostgresDatabase(dsn)
    prefix = f"metrics_{uuid4().hex[:12]}"
    ids: dict[str, str] = {}

    def insert(table, **values) -> str:
        return store().query_write(
            table.insert().values(**values).returning(table.c.id)
        )[0]["id"]

    existing = store().query_read(
        select(s.catalog_database.c.id).where(
            s.catalog_database.c.name == connector.database_name
        )
    )
    db_id = (
        existing[0]["id"]
        if existing
        else insert(
            s.catalog_database, name=connector.database_name, dialect="postgres"
        )
    )
    schema_id = insert(s.catalog_schema, database_id=db_id, name=prefix)
    table_id = insert(s.catalog_table, schema_id=schema_id, name="orders", pk=["id"])
    term_id = insert(s.term, name=prefix, source="semantic")
    physical = f'"{prefix}"."orders"'
    connector.execute(f'CREATE SCHEMA "{prefix}"')
    connector.execute(
        f"CREATE TABLE {physical} (id integer PRIMARY KEY, amount numeric NOT NULL, status text NOT NULL)"
    )
    connector.execute(
        f"INSERT INTO {physical} VALUES (1, 250, 'confirmed'), (2, 100, 'confirmed'), (3, 50, 'cancelled')"
    )
    fields = []
    for name, dtype, physical_type in (
        ("id", "number", "integer"),
        ("amount", "number", "numeric"),
        ("status", "string", "text"),
    ):
        column_id = insert(
            s.catalog_column,
            table_id=table_id,
            name=name,
            data_type=physical_type,
            is_nullable=False,
            is_unique=name == "id",
        )
        ids[name] = column_id
        fields.append(
            FieldBinding(
                field_id=name,
                catalog_column_id=column_id,
                column_name=name,
                data_type=dtype,
                nullable=False,
            )
        )
    source = SourceBinding(
        source_id="orders",
        datasource_id=db_id,
        catalog_table_id=table_id,
        owner_term_id=term_id,
        schema_name=prefix,
        table_name="orders",
        grain=("id",),
        fields=tuple(fields),
    )
    predicate = Predicate(
        field=FieldRef(source_id="orders", field_id="status"),
        operator="eq",
        value="confirmed",
    )
    model = GovernedModel(
        model_id=prefix,
        name="Confirmed orders",
        sources=(source,),
        metrics=(
            Metric(
                metric_id="order_count",
                name="Order count",
                description="Confirmed order row count",
                owner_term_id=term_id,
                source_ids=("orders",),
                aggregation="count",
                required_filters=(predicate,),
                empty_value="zero",
            ),
            Metric(
                metric_id="amount",
                name="Amount",
                description="Confirmed amount",
                owner_term_id=term_id,
                source_ids=("orders",),
                aggregation="sum",
                expression=FieldExpression(field_id="amount"),
                required_filters=(predicate,),
            ),
        ),
    )
    monkeypatch.setattr(service, "get_connectors", lambda: [connector])
    monkeypatch.setenv("AUTO_ONTOLOGY_GOVERNED_METRICS_ENABLED", "true")
    yield model, connector, physical, ids
    store().query_write(
        s.governed_model.delete().where(s.governed_model.c.model_id.like(f"{prefix}%"))
    )
    store().query_write(s.term.delete().where(s.term.c.id == term_id))
    store().query_write(
        s.catalog_schema.delete().where(s.catalog_schema.c.id == schema_id)
    )
    if not existing:
        store().query_write(
            s.catalog_database.delete().where(s.catalog_database.c.id == db_id)
        )
    connector.execute(f'DROP SCHEMA "{prefix}" CASCADE')
    connector.close()


def publish(model: GovernedModel) -> dict:
    row = service.create(model, user_id="reviewer", owner_id=None)
    row = service.transition(
        model.model_id,
        revision=1,
        expected_version=row["version"],
        action="validate",
        user_id="reviewer",
    )
    assert row["revisions"][0]["validation"]["valid"] is True
    row = service.transition(
        model.model_id,
        revision=1,
        expected_version=row["version"],
        action="certify",
        user_id="reviewer",
        evidence="Reviewed against fixture: two confirmed orders, amount 350",
    )
    return service.transition(
        model.model_id,
        revision=1,
        expected_version=row["version"],
        action="activate",
        user_id="reviewer",
    )


def test_lifecycle_uses_bound_parameters_and_keeps_active_revision(catalog) -> None:
    model, _, _, _ = catalog
    row = publish(model)
    query = MetricQueryRequest(metric_ids=("order_count", "amount"), all_time=True)
    result = service.execute(model.model_id, query)
    assert result["trusted"] is True
    assert result["rows"] == [{"order_count": 2, "amount": "350"}]
    assert "confirmed" not in result["plan"]["sql"]
    assert "confirmed" in result["plan"]["params"].values()
    attack = "confirmed' OR true --"
    injection_query = MetricQueryRequest(
        metric_ids=("order_count",),
        all_time=True,
        filters=(
            Predicate(
                field=FieldRef(source_id="orders", field_id="status"),
                operator="eq",
                value=attack,
            ),
        ),
    )
    injected = service.execute(model.model_id, injection_query)
    assert injected["rows"] == [{"order_count": 0}]
    assert attack not in injected["plan"]["sql"]
    draft = model.model_copy(update={"name": "Revised business definition"})
    saved = service.draft(
        model.model_id, draft, expected_version=row["version"], user_id="editor"
    )
    assert saved["active_revision"] == 1
    assert saved["revisions"][0]["status"] == "draft"
    assert saved["revisions"][0]["certification"] is None
    assert saved["revisions"][1]["payload"] == model.model_dump(mode="json")
    assert service.execute(model.model_id, query)["revision"] == 1
    with pytest.raises(ContractError, match="active"):
        service.execute(model.model_id, query, revision=2)
    preview = service.compile_model(model.model_id, query, revision=2, preview=True)
    assert preview["trusted"] is False
    revoked = service.transition(
        model.model_id,
        revision=1,
        expected_version=saved["version"],
        action="deprecate",
        user_id="reviewer",
    )
    assert revoked["active_revision"] is None
    with pytest.raises(ContractError, match="active"):
        service.execute(model.model_id, query)


def test_live_source_drift_blocks_until_a_new_review(catalog) -> None:
    model, connector, physical, _ = catalog
    row = publish(model)
    connector.execute(f"ALTER TABLE {physical} ADD COLUMN new_business_field text")
    with pytest.raises(ContractError) as changed:
        service.execute(
            model.model_id,
            MetricQueryRequest(metric_ids=("order_count",), all_time=True),
        )
    assert changed.value.code == "SOURCE_CHANGED"
    # Re-validating or re-certifying the old payload cannot launder old authority.
    with pytest.raises(ContractError) as recertify:
        service.transition(
            model.model_id,
            revision=1,
            expected_version=row["version"],
            action="certify",
            user_id="reviewer",
            evidence="again",
        )
    assert recertify.value.code == "INVALID_TRANSITION"
    assert dal.get_model(model.model_id)["version"] == row["version"]


def test_optimistic_updates_have_exactly_one_winner(catalog) -> None:
    model, _, _, _ = catalog
    service.create(model, user_id="editor", owner_id=None)

    def append(name: str) -> str:
        try:
            service.draft(
                model.model_id,
                model.model_copy(update={"name": name}),
                expected_version=1,
                user_id="editor",
            )
            return "saved"
        except dal.VersionConflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(append, ("A", "B")))
    assert sorted(results) == ["conflict", "saved"]
    row = dal.detail(model.model_id)
    assert row["version"] == 2
    assert [revision["revision"] for revision in row["revisions"]] == [2, 1]


def test_catalog_ids_and_complete_keys_are_enforced(catalog) -> None:
    model, _, _, _ = catalog
    source = model.sources[0]
    wrong = source.model_copy(update={"table_name": "other"})
    with pytest.raises(ContractError) as mismatch:
        service.create(
            model.model_copy(update={"sources": (wrong,)}),
            user_id="editor",
            owner_id=None,
        )
    assert mismatch.value.code == "BINDING_MISMATCH"
    wrong = source.model_copy(update={"grain": ("status",)})
    with pytest.raises(ContractError) as grain:
        service.create(
            model.model_copy(update={"sources": (wrong,)}),
            user_id="editor",
            owner_id=None,
        )
    assert grain.value.code == "UNPROVEN_GRAIN"
    metric = model.metrics[0].model_copy(update={"owner_term_id": "missing-term"})
    with pytest.raises(ContractError) as term:
        service.create(
            model.model_copy(update={"metrics": (metric,)}),
            user_id="editor",
            owner_id=None,
        )
    assert term.value.code == "UNKNOWN_TERM"


def test_api_permissions_import_provenance_and_flag(
    catalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    model, _, _, _ = catalog
    publish(model)
    monkeypatch.setattr(
        api,
        "get_user_role",
        lambda user_id: "admin" if user_id == "reviewer" else "viewer",
    )
    monkeypatch.setattr(
        api, "can_edit_entity", lambda *, user_id, subject: user_id == "reviewer"
    )
    app = FastAPI()
    app.include_router(api.router, prefix="/api")
    client = TestClient(app)
    route = f"/api/metrics/models/{model.model_id}"
    reviewer = {"x-auto-ontology-user-id": "reviewer"}
    viewer = {"x-auto-ontology-user-id": "viewer"}
    assert client.get("/api/metrics/models").status_code == 401
    assert client.get("/api/metrics/models", headers=viewer).status_code == 200
    for unsafe_id in ("sales/2026", ".", "..", "指标", "a" * 129):
        rejected = client.post(
            "/api/metrics/models",
            headers=reviewer,
            json={"model": {**model.model_dump(mode="json"), "model_id": unsafe_id}},
        )
        assert rejected.status_code == 422
        assert rejected.json()["detail"]["code"] == "INVALID_MODEL_ID"
    exported = client.get(f"{route}/export", headers=viewer).json()["data"]
    exported["model"]["model_id"] += "_import"
    imported = client.post("/api/metrics/import", headers=reviewer, json=exported)
    assert imported.status_code == 201, imported.text
    revision = imported.json()["data"]["revisions"][0]
    assert revision["status"] == "draft"
    assert revision["certification"] is None
    assert imported.json()["data"]["active_revision"] is None
    query = {"query": {"metric_ids": ["order_count"], "all_time": True}}
    assert (
        client.post(
            f"{route}/compile", headers=viewer, json={**query, "preview": True}
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"{route}/execute", headers=reviewer, json={**query, "preview": True}
        ).status_code
        == 422
    )
    assert (
        client.post(
            f"{route}/certify",
            headers=viewer,
            json={"revision": 1, "expected_version": 4, "evidence": "spoof"},
        ).status_code
        == 403
    )
    monkeypatch.delenv("AUTO_ONTOLOGY_GOVERNED_METRICS_ENABLED")
    assert client.get("/api/metrics/config", headers=viewer).json() == {
        "data": {"enabled": True}
    }
    monkeypatch.setenv("AUTO_ONTOLOGY_GOVERNED_METRICS_ENABLED", "false")
    assert client.get("/api/metrics/config", headers=viewer).json() == {
        "data": {"enabled": False}
    }
    disabled = client.post(f"{route}/execute", headers=viewer, json=query)
    assert disabled.status_code == 503
    assert disabled.json()["detail"]["code"] == "FEATURE_DISABLED"


def test_exact_decimal_nullable_bigint_and_empty_columns(catalog) -> None:
    model, connector, physical, _ = catalog
    connector.execute(
        f"UPDATE {physical} SET amount = 9007199254740993.01 WHERE id = 1"
    )
    publish(model)
    result = service.execute(
        model.model_id, MetricQueryRequest(metric_ids=("amount",), all_time=True)
    )
    assert result["rows"] == [{"amount": "9007199254741093.01"}]
    assert result["column_metadata"][0]["json_encoding"] == "decimal-string"
    with connector.read_transaction() as read:
        # Object dtype is essential here: a nullable bigint otherwise becomes
        # float64 before the service ever sees the original psycopg integer.
        frame = read(
            "SELECT * FROM (VALUES (9007199254740993::bigint, 0.123456789012345678::numeric), (NULL, NULL)) AS v(big, ratio)",
            None,
        )
        result = service.serialize_frame(frame)
        assert result["rows"] == [
            {"big": "9007199254740993", "ratio": "0.123456789012345678"},
            {"big": None, "ratio": None},
        ]
        empty = service.serialize_frame(
            read("SELECT 1::bigint AS big, 1::numeric AS ratio WHERE FALSE", None)
        )
        assert empty["columns"] == ["big", "ratio"]
        assert empty["rows"] == []
        assert [column["postgres_type_oid"] for column in empty["column_metadata"]] == [
            20,
            1700,
        ]


def test_naive_timestamps_are_rejected_and_aware_range_is_timezone_independent(
    catalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import datetime
    from psycopg.conninfo import make_conninfo
    from auto_ontology.semantic.contracts.models import TimeRange

    model, connector, physical, _ = catalog
    source = model.sources[0]
    connector.execute(
        f"ALTER TABLE {physical} ADD COLUMN occurred_at timestamp without time zone NOT NULL DEFAULT '2025-01-01 00:30:00'"
    )
    column_id = store().query_write(
        s.catalog_column.insert()
        .values(
            table_id=source.catalog_table_id,
            name="occurred_at",
            data_type="timestamp without time zone",
            is_nullable=False,
        )
        .returning(s.catalog_column.c.id)
    )[0]["id"]
    field = FieldBinding(
        field_id="occurred_at",
        catalog_column_id=column_id,
        column_name="occurred_at",
        data_type="datetime",
        nullable=False,
    )
    source = source.model_copy(update={"fields": (*source.fields, field)})
    metric = model.metrics[0].model_copy(
        update={
            "time_field_id": "occurred_at",
            "time_role": "occurred",
            "supported_granularities": ("day",),
        }
    )
    model = model.model_copy(update={"sources": (source,), "metrics": (metric,)})
    with pytest.raises(ContractError) as naive:
        service.create(model, user_id="reviewer", owner_id=None)
    assert naive.value.code == "NAIVE_TIMESTAMP_UNSUPPORTED"
    connector.execute(
        f"ALTER TABLE {physical} ALTER COLUMN occurred_at TYPE timestamptz USING occurred_at AT TIME ZONE 'UTC'"
    )
    connector.execute(
        f"UPDATE {physical} SET occurred_at = '2025-01-01 23:30:00+00' WHERE id = 2"
    )
    store().query_write(
        s.catalog_column.update()
        .where(s.catalog_column.c.id == column_id)
        .values(data_type="timestamp with time zone")
    )
    publish(model)
    query = MetricQueryRequest(
        metric_ids=("order_count",),
        time=TimeRange(
            start=datetime.fromisoformat("2025-01-01T00:00:00+00:00"),
            end_exclusive=datetime.fromisoformat("2025-01-02T00:00:00+00:00"),
            timezone="UTC",
            time_role="occurred",
            granularity="day",
        ),
    )
    result = service.execute(model.model_id, query)
    assert result["rows"][0]["order_count"] == 2
    hawaii = PostgresDatabase(
        make_conninfo(
            os.environ["AUTO_ONTOLOGY_GOVERNED_TEST_DSN"],
            options="-c timezone=Pacific/Honolulu",
        )
    )
    try:
        monkeypatch.setattr(service, "get_connectors", lambda: [hawaii])
        assert service.execute(model.model_id, query)["rows"] == result["rows"]
    finally:
        hawaii.close()


def test_read_transaction_rejects_writes_and_keeps_snapshot(catalog) -> None:
    _, connector, physical, _ = catalog
    import psycopg

    with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
        with connector.read_transaction() as read:
            read(f"DELETE FROM {physical}", None)
    with connector.read_transaction() as read:
        first = read(f"SELECT COUNT(*) AS n FROM {physical}", None).iloc[0, 0]
        connector.execute(f"INSERT INTO {physical} VALUES (4, 999, 'confirmed')")
        second = read(f"SELECT COUNT(*) AS n FROM {physical}", None).iloc[0, 0]
        assert first == second == 3
    assert connector.execute(f"SELECT COUNT(*) AS n FROM {physical}").iloc[0, 0] == 4
