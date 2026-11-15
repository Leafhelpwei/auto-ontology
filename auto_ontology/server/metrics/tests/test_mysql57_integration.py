# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Real MySQL 5.7 sources, PostgreSQL governance, and authenticated metric Chat.

Only UUID-prefixed tables/records created by these fixtures are removed. The
source account used by the application has SELECT only; the separate fixture
account is used exclusively for setup, deliberate drift, and cleanup. Natural
language responses are deterministic stubs, never evidence of live LLM quality.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import json
import os
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
import mysql.connector
import psycopg
from psycopg.rows import dict_row
import pytest
from sqlalchemy import select

from auto_ontology.connectors.mysql import MySQLDatabase, _parse_connection_string
from auto_ontology.dal import schema as s
from auto_ontology.dal.session import store
from auto_ontology.semantic.contracts.models import (
    ContractError,
    GovernedModel,
    MetricQueryRequest,
)
from auto_ontology.server.chat import metrics_flow
from auto_ontology.server.chat.conversation_dal import (
    ConversationAccessError,
    persist_assistant_result,
    persist_result_message,
    prepare_conversation,
)
from auto_ontology.server.metrics import interpretation
from auto_ontology.server.metrics import router as metrics_api
from auto_ontology.server.metrics import service


STRICT_MODE = (
    "ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,NO_ZERO_DATE,NO_ZERO_IN_DATE,"
    "ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION"
)
EXACT_AMOUNT = Decimal("10000000000000000000.123457")


@dataclass
class MySQLCatalog:
    model: GovernedModel
    connector: MySQLDatabase
    admin_kwargs: dict
    physical: str
    admin_user: str
    viewer_user: str
    pg_dsn: str
    table_id: str

    def mutate(self, statement: str, parameters=None) -> None:
        connection = mysql.connector.connect(**self.admin_kwargs)
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(statement, parameters)
                connection.commit()
            finally:
                cursor.close()
        finally:
            connection.close()


@pytest.fixture
def mysql57_catalog(monkeypatch: pytest.MonkeyPatch):
    reader_dsn = os.environ.get("AUTO_ONTOLOGY_MYSQL57_TEST_DSN")
    admin_dsn = os.environ.get("AUTO_ONTOLOGY_MYSQL57_ADMIN_DSN")
    pg_dsn = os.environ.get("AUTO_ONTOLOGY_GOVERNED_TEST_DSN")
    if not all((reader_dsn, admin_dsn, pg_dsn)):
        pytest.skip(
            "Requires isolated MySQL 5.7 reader/fixture accounts and migrated PostgreSQL"
        )
    reader_kwargs = _parse_connection_string(reader_dsn)
    admin_kwargs = _parse_connection_string(admin_dsn)
    assert reader_kwargs["database"] == admin_kwargs["database"]
    assert reader_kwargs["user"] != admin_kwargs["user"]
    admin_kwargs.update(
        sql_mode=STRICT_MODE,
        time_zone="+00:00",
        charset="utf8mb4",
        collation="utf8mb4_unicode_ci",
        use_pure=True,
    )
    connector = MySQLDatabase(reader_dsn)
    connector._connect_kwargs["sql_mode"] = STRICT_MODE
    version = str(connector.execute("SELECT VERSION() AS version").iloc[0, 0])
    assert version.startswith("5.7."), "This regression must run on actual MySQL 5.7"
    prefix = f"m57_{uuid4().hex[:16]}"
    database = connector.database_name
    table_name = f"{prefix}_orders"
    physical = f"`{database}`.`{table_name}`"
    ids: dict[str, str] = {}
    created_db = created_schema = False
    created_table = False
    users = [f"{prefix}_admin", f"{prefix}_viewer"]

    def insert(table, **values) -> str:
        return store().query_write(
            table.insert().values(**values).returning(table.c.id)
        )[0]["id"]

    def mutate(statement: str, parameters=None) -> None:
        connection = mysql.connector.connect(**admin_kwargs)
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(statement, parameters)
                connection.commit()
            finally:
                cursor.close()
        finally:
            connection.close()

    try:
        mutate(
            f"CREATE TABLE {physical} ("
            "id BIGINT UNSIGNED PRIMARY KEY, amount DECIMAL(30,6) NULL, "
            "status VARCHAR(32) NOT NULL, category VARCHAR(32) NULL, "
            "business_date DATE NOT NULL, occurred TIMESTAMP(6) NULL, "
            "local_time DATETIME(6) NULL) ENGINE=InnoDB"
        )
        created_table = True
        for values in (
            (
                1,
                "10000000000000000000.123456",
                "confirmed",
                None,
                "2026-10-31",
                "2026-10-31 16:00:00",
                "2026-11-01 00:00:00",
            ),
            (
                9007199254740993,
                "0.000001",
                "confirmed",
                "A",
                "2026-11-01",
                "2026-11-01 15:59:59.999999",
                "2026-11-01 23:59:59.999999",
            ),
            (
                3,
                None,
                "cancelled",
                "A",
                "2026-11-02",
                "2026-11-01 16:00:00",
                "2026-11-02 00:00:00",
            ),
            (
                4,
                "50.500000",
                "cancelled",
                "B",
                "2026-11-02",
                "2026-11-02 00:00:00",
                "2026-11-02 08:00:00",
            ),
        ):
            mutate(f"INSERT INTO {physical} VALUES (%s,%s,%s,%s,%s,%s,%s)", values)
        existing = store().query_read(
            select(s.catalog_database.c.id, s.catalog_database.c.dialect).where(
                s.catalog_database.c.name == database
            )
        )
        if existing:
            assert existing[0]["dialect"] == "mysql"
            ids["database"] = existing[0]["id"]
        else:
            ids["database"] = insert(s.catalog_database, name=database, dialect="mysql")
            created_db = True
        schemas = store().query_read(
            select(s.catalog_schema.c.id).where(
                s.catalog_schema.c.database_id == ids["database"],
                s.catalog_schema.c.name == database,
            )
        )
        if schemas:
            ids["schema"] = schemas[0]["id"]
        else:
            ids["schema"] = insert(
                s.catalog_schema, database_id=ids["database"], name=database
            )
            created_schema = True
        ids["table"] = insert(
            s.catalog_table, schema_id=ids["schema"], name=table_name, pk=["id"]
        )
        ids["term"] = insert(s.term, name=prefix, source="semantic")
        fields = []
        for name, physical_type, contract_type, nullable, zone in (
            ("id", "bigint unsigned", "number", False, None),
            ("amount", "decimal(30,6)", "number", True, None),
            ("status", "varchar(32)", "string", False, None),
            ("category", "varchar(32)", "string", True, None),
            ("business_date", "date", "date", False, None),
            ("occurred", "timestamp(6)", "datetime", True, "UTC"),
            ("local_time", "datetime(6)", "datetime", True, "+08:00"),
        ):
            column_id = insert(
                s.catalog_column,
                table_id=ids["table"],
                name=name,
                data_type=physical_type,
                is_nullable=nullable,
                is_unique=name == "id",
            )
            field = dict(
                field_id=name,
                catalog_column_id=column_id,
                column_name=name,
                data_type=contract_type,
                nullable=nullable,
            )
            if zone:
                field["datetime_timezone"] = zone
            fields.append(field)
        common = dict(
            owner_term_id=ids["term"],
            source_ids=["orders"],
            supported_dimensions=["category", "order_id"],
            time_field_id="business_date",
            time_role="event",
            supported_granularities=["day", "month", "year"],
        )
        confirmed = dict(
            field=dict(source_id="orders", field_id="status"),
            operator="eq",
            value="confirmed",
        )
        cancelled = {**confirmed, "value": "cancelled"}
        metrics = [
            dict(
                **common,
                metric_id="confirmed_count",
                name="Confirmed count",
                description="Confirmed row count",
                aggregation="count",
                required_filters=[confirmed],
                empty_value="zero",
            ),
            dict(
                **common,
                metric_id="confirmed_amount",
                name="Confirmed amount",
                description="Exact confirmed amount",
                aggregation="sum",
                expression=dict(kind="field", field_id="amount"),
                required_filters=[confirmed],
            ),
            dict(
                **common,
                metric_id="cancelled_amount",
                name="Cancelled amount",
                description="Cancelled amount with explicit zero",
                aggregation="sum",
                expression=dict(kind="field", field_id="amount"),
                required_filters=[cancelled],
                empty_value="zero",
            ),
            dict(
                **common,
                metric_id="average_amount",
                name="Average amount",
                description="Ratio of governed aggregates",
                kind="composite",
                expression=dict(
                    kind="binary",
                    operator="divide",
                    left=dict(kind="metric", metric_id="confirmed_amount"),
                    right=dict(kind="metric", metric_id="confirmed_count"),
                ),
            ),
            dict(
                **{**common, "time_field_id": "occurred"},
                metric_id="timestamp_count",
                name="Timestamp count",
                description="UTC-normalized timestamp",
                aggregation="count",
                empty_value="zero",
            ),
            dict(
                **{**common, "time_field_id": "local_time"},
                metric_id="local_count",
                name="Local datetime count",
                description="Explicit fixed-offset datetime",
                aggregation="count",
                empty_value="zero",
            ),
        ]
        model = GovernedModel.model_validate(
            dict(
                model_id=prefix,
                name="MySQL 5.7 isolated regression",
                dialect="mysql",
                sources=[
                    dict(
                        source_id="orders",
                        datasource_id=ids["database"],
                        catalog_table_id=ids["table"],
                        owner_term_id=ids["term"],
                        schema_name=database,
                        table_name=table_name,
                        grain=["id"],
                        fields=fields,
                        dimensions=[
                            dict(
                                dimension_id="category",
                                field=dict(source_id="orders", field_id="category"),
                            ),
                            dict(
                                dimension_id="order_id",
                                field=dict(source_id="orders", field_id="id"),
                            ),
                        ],
                    )
                ],
                metrics=metrics,
            )
        )
        with psycopg.connect(pg_dsn) as conn:
            for user_id, role in zip(users, ("admin", "viewer"), strict=True):
                conn.execute(
                    'INSERT INTO frontend."user" (id,name,email,role,"updatedAt") VALUES (%s,%s,%s,%s,NOW())',
                    (user_id, user_id, f"{user_id}@example.test", role),
                )
        monkeypatch.setattr(service, "get_connectors", lambda: [connector])
        monkeypatch.setenv("AUTO_ONTOLOGY_GOVERNED_METRICS_ENABLED", "true")
        yield MySQLCatalog(
            model, connector, admin_kwargs, physical, *users, pg_dsn, ids["table"]
        )
    finally:
        # Exact UUID-scoped identities, never a broad delete or shared DB drop.
        store().query_write(
            s.governed_model.delete().where(
                s.governed_model.c.model_id.in_([prefix, f"{prefix}_alternate"])
            )
        )
        if "term" in ids:
            store().query_write(s.term.delete().where(s.term.c.id == ids["term"]))
        if "table" in ids:
            store().query_write(
                s.catalog_table.delete().where(s.catalog_table.c.id == ids["table"])
            )
        if created_schema:
            store().query_write(
                s.catalog_schema.delete().where(s.catalog_schema.c.id == ids["schema"])
            )
        if created_db:
            store().query_write(
                s.catalog_database.delete().where(
                    s.catalog_database.c.id == ids["database"]
                )
            )
        with psycopg.connect(pg_dsn) as conn:
            conn.execute('DELETE FROM frontend."user" WHERE id = ANY(%s)', (users,))
        if created_table:
            mutate(f"DROP TABLE {physical}")
        connector.close()


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(metrics_api.router)
    return TestClient(app)


def _headers(user_id: str) -> dict[str, str]:
    return {"x-auto-ontology-user-id": user_id}


def _publish(fixture: MySQLCatalog) -> dict:
    with _client() as client:
        response = client.post(
            "/metrics/models",
            headers=_headers(fixture.admin_user),
            json={"model": fixture.model.model_dump(mode="json")},
        )
        assert response.status_code == 201, response.text
        row = response.json()["data"]
        for action in ("validate", "certify", "activate"):
            response = client.post(
                f"/metrics/models/{fixture.model.model_id}/{action}",
                headers=_headers(fixture.admin_user),
                json={
                    "revision": 1,
                    "expected_version": row["version"],
                    "evidence": "Isolated fixture amounts reviewed; no production source",
                },
            )
            assert response.status_code == 200, response.text
            row = response.json()["data"]
            if action == "validate":
                assert row["revisions"][0]["validation"]["valid"] is True
        assert row["active_revision"] == 1
        return row


def test_mysql57_authenticated_lifecycle_exact_rows_and_bound_query(
    mysql57_catalog,
) -> None:
    fixture = mysql57_catalog
    with _client() as client:
        assert client.get("/metrics/models").status_code == 401
        assert (
            client.get("/metrics/models", headers=_headers("unknown-user")).status_code
            == 403
        )
        assert (
            client.post(
                "/metrics/models",
                headers=_headers(fixture.viewer_user),
                json={"model": fixture.model.model_dump(mode="json")},
            ).status_code
            == 403
        )
    _publish(fixture)
    query = MetricQueryRequest(
        metric_ids=("confirmed_count", "confirmed_amount", "average_amount"),
        all_time=True,
    )
    with _client() as client:
        body = {"query": query.model_dump(mode="json"), "revision": 1}
        compiled = client.post(
            f"/metrics/models/{fixture.model.model_id}/compile",
            headers=_headers(fixture.viewer_user),
            json=body,
        )
        assert compiled.status_code == 200, compiled.text
        sql = compiled.json()["data"]["plan"]["sql"]
        assert "WITH " not in sql.upper() and "confirmed'" not in sql
        response = client.post(
            f"/metrics/models/{fixture.model.model_id}/execute",
            headers=_headers(fixture.viewer_user),
            json=body,
        )
        assert response.status_code == 200, response.text
    result = response.json()["data"]
    assert result["trusted"] and result["rows"][0]["confirmed_count"] == 2
    assert isinstance(result["rows"][0]["confirmed_amount"], str)
    assert Decimal(result["rows"][0]["confirmed_amount"]) == EXACT_AMOUNT
    assert Decimal(result["rows"][0]["average_amount"]) == EXACT_AMOUNT / 2
    assert all(
        column["dialect"] == "mysql" and column["postgres_type_oid"] is None
        for column in result["column_metadata"]
    )
    attack = "confirmed' OR 1=1 --"
    injected = service.execute(
        fixture.model.model_id,
        MetricQueryRequest.model_validate(
            {
                "metric_ids": ["confirmed_count", "confirmed_amount"],
                "all_time": True,
                "filters": [
                    {
                        "field": {"source_id": "orders", "field_id": "status"},
                        "operator": "eq",
                        "value": attack,
                    }
                ],
            }
        ),
    )
    assert injected["rows"] == [{"confirmed_count": 0, "confirmed_amount": None}]
    assert (
        attack not in injected["plan"]["sql"]
        and attack in injected["plan"]["params"].values()
    )


def test_mysql57_null_dimension_union_group_by_and_unsigned_precision(
    mysql57_catalog,
) -> None:
    fixture = mysql57_catalog
    _publish(fixture)
    mode = fixture.connector.execute("SELECT @@session.sql_mode AS mode").iloc[0, 0]
    assert "ONLY_FULL_GROUP_BY" in mode
    query = MetricQueryRequest.model_validate(
        {
            "metric_ids": [
                "confirmed_count",
                "confirmed_amount",
                "cancelled_amount",
                "average_amount",
            ],
            "dimensions": ["category"],
            "all_time": True,
            "order_by": [{"field_id": "category", "direction": "asc"}],
        }
    )
    result = service.execute(fixture.model.model_id, query)
    assert [row["category"] for row in result["rows"]] == ["A", "B", None]
    rows = {row["category"]: row for row in result["rows"]}
    assert rows["B"]["confirmed_count"] == 0 and rows["B"]["confirmed_amount"] is None
    assert rows["B"]["average_amount"] is None
    assert Decimal(rows["B"]["cancelled_amount"]) == Decimal("50.5")
    assert rows[None]["confirmed_count"] == 1
    large = service.execute(
        fixture.model.model_id,
        MetricQueryRequest.model_validate(
            {
                "metric_ids": ["confirmed_count"],
                "dimensions": ["order_id"],
                "all_time": True,
                "filters": [
                    {
                        "field": {"source_id": "orders", "field_id": "id"},
                        "operator": "eq",
                        "value": "9007199254740993",
                    }
                ],
            }
        ),
    )
    assert large["rows"] == [{"order_id": "9007199254740993", "confirmed_count": 1}]


def test_mysql57_date_and_timestamp_local_range_agree(mysql57_catalog) -> None:
    fixture = mysql57_catalog
    _publish(fixture)
    period = dict(
        start="2026-11-01T00:00:00+08:00",
        end_exclusive="2026-11-02T00:00:00+08:00",
        timezone="Asia/Shanghai",
        time_role="event",
        granularity="day",
    )
    for metric_id, count in (
        ("timestamp_count", 2),
        ("local_count", 2),
        ("confirmed_count", 1),
    ):
        result = service.execute(
            fixture.model.model_id,
            MetricQueryRequest.model_validate(
                {"metric_ids": [metric_id], "time": period}
            ),
        )
        assert len(result["rows"]) == 1
        assert result["rows"][0][metric_id] == count
        assert str(result["rows"][0]["__time"]).startswith("2026-11-01")


@pytest.mark.parametrize(
    "change",
    (
        "ADD COLUMN unexpected_business_column INT NULL",
        "MODIFY amount DECIMAL(31,6) NULL",
    ),
)
def test_mysql57_schema_drift_and_catalog_route_mismatch_fail_closed(
    mysql57_catalog,
    change: str,
) -> None:
    fixture = mysql57_catalog
    _publish(fixture)
    original = fixture.model.sources[0]
    altered = fixture.model.model_copy(
        update={
            "model_id": f"{fixture.model.model_id}_alternate",
            "sources": (
                original.model_copy(update={"schema_name": "unrelated_database"}),
            ),
        }
    )
    with pytest.raises(ContractError) as mismatch:
        service.create(altered, user_id=fixture.admin_user, owner_id=None)
    assert mismatch.value.code == "BINDING_MISMATCH"
    fixture.mutate(f"ALTER TABLE {fixture.physical} {change}")
    with pytest.raises(ContractError) as drift:
        service.execute(
            fixture.model.model_id,
            MetricQueryRequest(metric_ids=("confirmed_count",), all_time=True),
        )
    assert drift.value.code == "SOURCE_CHANGED"


def test_mysql57_prefix_unique_does_not_prove_composite_grain(mysql57_catalog) -> None:
    fixture = mysql57_catalog
    fixture.mutate(
        f"ALTER TABLE {fixture.physical} ADD COLUMN code VARCHAR(32) NOT NULL DEFAULT ''"
    )
    fixture.mutate(f"UPDATE {fixture.physical} SET code = CONCAT('record_', id)")
    fixture.mutate(
        f"ALTER TABLE {fixture.physical} ADD UNIQUE KEY prefix_only (code(24), status)"
    )
    column_id = store().query_write(
        s.catalog_column.insert()
        .values(
            table_id=fixture.table_id,
            name="code",
            data_type="varchar(32)",
            is_nullable=False,
            is_unique=False,
        )
        .returning(s.catalog_column.c.id)
    )[0]["id"]
    # Deliberately optimistic Catalog metadata cannot replace physical proof.
    store().query_write(
        s.catalog_table.update()
        .where(s.catalog_table.c.id == fixture.table_id)
        .values(pk=["code", "status"])
    )
    data = fixture.model.model_dump(mode="json")
    data["sources"][0]["grain"] = ["code", "status"]
    data["sources"][0]["fields"].append(
        dict(
            field_id="code",
            catalog_column_id=column_id,
            column_name="code",
            data_type="string",
            nullable=False,
        )
    )
    fixture.model = GovernedModel.model_validate(data)
    with pytest.raises(ContractError) as prefix:
        service.create(fixture.model, user_id=fixture.admin_user, owner_id=None)
    assert prefix.value.code == "UNPROVEN_GRAIN"
    fixture.mutate(
        f"ALTER TABLE {fixture.physical} DROP INDEX prefix_only, "
        "DROP PRIMARY KEY, ADD PRIMARY KEY (code, status)"
    )
    _publish(fixture)
    result = service.execute(
        fixture.model.model_id,
        MetricQueryRequest(metric_ids=("confirmed_count",), all_time=True),
    )
    assert result["rows"] == [{"confirmed_count": 2}]


def test_mysql57_chat_real_permissions_context_and_result_persistence(
    mysql57_catalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = mysql57_catalog
    _publish(fixture)
    scope = dict(
        version=1,
        query_mode="metrics",
        model_id=fixture.model.model_id,
        revision=1,
        selected_metric_ids=["confirmed_count", "confirmed_amount"],
    )
    conversation_id = uuid4()
    first = prepare_conversation(
        conversation_id=conversation_id,
        user_id=fixture.viewer_user,
        question="确认订单数量和金额",
        source="mysql57-regression",
        query_context=scope,
    )
    monkeypatch.setattr(interpretation, "get_llm_client", lambda: object())
    monkeypatch.setattr(
        interpretation,
        "invoke_with_structured_output",
        lambda *a, **k: interpretation.InterpretationCandidate(
            clarification="请选择时间范围"
        ),
    )
    payload = dict(
        question="确认订单数量和金额",
        metric_context=scope,
        user_id=fixture.viewer_user,
        persistent=True,
        turn_id=first.analytics_id,
        conversation_history=[],
    )
    answer = list(metrics_flow.stream_metric_response(payload))[-1]["answer"]
    assert (
        answer["query_context"]["status"] == "clarification"
        and "sql_code" not in answer
    )
    persist_assistant_result(
        conversation_id=conversation_id,
        user_id=fixture.viewer_user,
        analytics_id=first.analytics_id,
        response=answer["response"],
        sql_code=None,
        query_context=answer["query_context"],
    )
    second = prepare_conversation(
        conversation_id=conversation_id,
        user_id=fixture.viewer_user,
        question="全部时间",
        source="mysql57-regression",
        query_context=scope,
    )
    query = MetricQueryRequest(
        metric_ids=("confirmed_count", "confirmed_amount"), all_time=True
    )
    captured = []

    def candidate(*args, **kwargs):
        captured.append(json.loads(args[1][1].content))
        return interpretation.InterpretationCandidate(query=query)

    monkeypatch.setattr(interpretation, "invoke_with_structured_output", candidate)
    answer = list(
        metrics_flow.stream_metric_response(
            {
                **payload,
                "question": "全部时间",
                "turn_id": second.analytics_id,
                "conversation_history": [vars(turn) for turn in second.history],
            }
        )
    )[-1]["answer"]
    assert captured[0]["context"]["model_fingerprint"] == fixture.model.fingerprint()
    assert answer["query_context"]["trusted"] is True
    assert (
        Decimal(json.loads(answer["sql_response_from_db"])[0]["confirmed_amount"])
        == EXACT_AMOUNT
    )
    persist_assistant_result(
        conversation_id=conversation_id,
        user_id=fixture.viewer_user,
        analytics_id=second.analytics_id,
        response=answer["response"],
        sql_code=answer["sql_code"],
        query_context=answer["query_context"],
    )
    persist_result_message(
        conversation_id=conversation_id,
        user_id=fixture.viewer_user,
        content="",
        sql_response=answer["sql_response_from_db"],
        query_context=answer["query_context"],
    )
    with psycopg.connect(fixture.pg_dsn, row_factory=dict_row) as connection:
        messages = connection.execute(
            "SELECT query_context,sql_response FROM frontend.messages WHERE conversation_id=%s ORDER BY created_at,id",
            (conversation_id,),
        ).fetchall()
    assert len(messages) == 5
    assert messages[-1]["query_context"]["turn_id"] == second.analytics_id
    assert messages[-1]["sql_response"] == answer["sql_response_from_db"]
    denied = list(
        metrics_flow.stream_metric_response({**payload, "user_id": "unknown-user"})
    )[-1]["answer"]
    assert denied["query_context"]["clarification"]["code"] == "METRIC_ACCESS_DENIED"
    with pytest.raises(ConversationAccessError):
        prepare_conversation(
            conversation_id=conversation_id,
            user_id=fixture.admin_user,
            question="foreign conversation",
            source="test",
            query_context=scope,
        )
