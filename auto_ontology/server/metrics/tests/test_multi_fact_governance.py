# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Publish and execute the multi-fact PoC through the real governed service."""

import os
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select
import pytest

from auto_ontology.connectors.mysql import MySQLDatabase
from auto_ontology.connectors.postgres import PostgresDatabase
from auto_ontology.dal import schema as s
from auto_ontology.dal.session import store
from auto_ontology.semantic.contracts.models import GovernedModel
from auto_ontology.semantic.contracts.tests.test_compiler_results import (
    postgres as postgres,
)
from auto_ontology.semantic.contracts.tests.test_mysql_results import (
    mysql_fixture as mysql_fixture,
)
from auto_ontology.semantic.contracts.tests.test_multi_fact import (
    multi as multi,
    query,
    gold,
)
from auto_ontology.server.metrics import service
from auto_ontology.server.metrics.source_adapters import adapter_for, source_transaction


@pytest.mark.parametrize("timed", [False, True])
def test_multi_fact_review_publish_execute_uses_one_governed_plan(
    multi, monkeypatch, timed
):
    original, _ = multi
    dialect = original.dialect
    connector = (
        PostgresDatabase(os.environ["AUTO_ONTOLOGY_GOVERNED_TEST_DSN"])
        if dialect == "postgres"
        else MySQLDatabase(os.environ["AUTO_ONTOLOGY_MYSQL57_TEST_DSN"])
    )
    database_name = connector.database_name
    monkeypatch.setattr(service, "get_connectors", lambda: [connector])

    def insert(table, **values):
        return store().query_write(
            table.insert().values(**values).returning(table.c.id)
        )[0]["id"]

    database = store().query_read(
        select(s.catalog_database.c.id).where(
            s.catalog_database.c.name == database_name
        )
    )
    db_id = (
        database[0]["id"]
        if database
        else insert(s.catalog_database, name=database_name, dialect=dialect)
    )
    schema_name = original.sources[0].schema_name
    schemas = store().query_read(
        select(s.catalog_schema.c.id).where(
            s.catalog_schema.c.database_id == db_id,
            s.catalog_schema.c.name == schema_name,
        )
    )
    schema_id = (
        schemas[0]["id"]
        if schemas
        else insert(s.catalog_schema, database_id=db_id, name=schema_name)
    )
    model_id = "multi_" + uuid4().hex
    term_id = insert(s.term, name=model_id, source="semantic")
    tables = []
    try:
        data = original.model_dump(mode="json")
        data["model_id"] = model_id
        adapter = adapter_for(dialect)
        with source_transaction(connector) as read:
            for binding, source_data in zip(
                original.sources, data["sources"], strict=True
            ):
                structure = adapter.inspect(read, binding)
                physical = {column["name"]: column for column in structure.columns}
                table_id = insert(
                    s.catalog_table,
                    schema_id=schema_id,
                    name=binding.table_name,
                    pk=[
                        next(
                            field.column_name
                            for field in binding.fields
                            if field.field_id == key
                        )
                        for key in binding.grain
                    ],
                )
                tables.append(table_id)
                source_data.update(
                    datasource_id=db_id,
                    catalog_table_id=table_id,
                    owner_term_id=term_id,
                )
                for field in source_data["fields"]:
                    column = physical[field["column_name"]]
                    field["catalog_column_id"] = insert(
                        s.catalog_column,
                        table_id=table_id,
                        name=field["column_name"],
                        data_type=column["data_type"],
                        is_nullable=column["nullable"],
                        is_unique=any(
                            key == [column["name"]] for key in structure.unique_keys
                        ),
                    )
        for metric in data["metrics"]:
            metric["owner_term_id"] = term_id
        model = GovernedModel.model_validate(data)
        row = service.create(model, user_id="fixture-reviewer", owner_id=None)
        for action in ("validate", "certify", "activate"):
            row = service.transition(
                model_id,
                revision=1,
                expected_version=row["version"],
                action=action,
                user_id="fixture-reviewer",
                evidence="Independent order/payment/refund decimal gold totals",
            )
            if action == "validate":
                assert row["revisions"][0]["validation"]["valid"], row["revisions"][0][
                    "validation"
                ]
        result = service.execute(model_id, query(timed=timed))
        assert result["trusted"] is True
        assert (
            result["plan"]["evidence"]["fact_aggregation"]
            == "independent_before_alignment"
        )
        expected = gold(timed)
        assert result["row_count"] == len(expected)
        for record in result["rows"]:
            for metric, value in expected[
                (int(record["customer"]), record["currency"])
            ].items():
                assert (
                    record[metric] is None
                    if value is None
                    else abs(Decimal(str(record[metric])) - value)
                    < Decimal("0.000000000000000001")
                )
    finally:
        store().query_write(
            s.governed_model.delete().where(s.governed_model.c.model_id == model_id)
        )
        store().query_write(
            s.catalog_table.delete().where(s.catalog_table.c.id.in_(tables))
        )
        store().query_write(s.term.delete().where(s.term.c.id == term_id))
        if not schemas:
            store().query_write(
                s.catalog_schema.delete().where(s.catalog_schema.c.id == schema_id)
            )
        if not database:
            store().query_write(
                s.catalog_database.delete().where(s.catalog_database.c.id == db_id)
            )
        connector.close()
