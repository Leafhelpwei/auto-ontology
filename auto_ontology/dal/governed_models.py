# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Immutable metric snapshots with serialized, optimistic lifecycle updates."""

from __future__ import annotations

from contextlib import contextmanager
from collections.abc import Iterator
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from auto_ontology.dal import schema as s
from auto_ontology.dal.session import store, write_transaction


class ModelNotFound(ValueError):
    """The model or revision does not exist."""


class VersionConflict(ValueError):
    """The caller edited a stale model version."""


def get_model(model_id: str) -> dict[str, Any] | None:
    rows = store().query_read(
        select(s.governed_model).where(s.governed_model.c.model_id == model_id)
    )
    return rows[0] if rows else None


def list_models() -> list[dict[str, Any]]:
    return store().query_read(
        select(s.governed_model)
        .where(~s.governed_model.c.archived)
        .order_by(s.governed_model.c.name, s.governed_model.c.model_id)
    )


def get_revision(model_id: str, revision: int) -> dict[str, Any] | None:
    rows = store().query_read(
        select(s.governed_model_revision).where(
            s.governed_model_revision.c.model_id == model_id,
            s.governed_model_revision.c.revision == revision,
        )
    )
    if not rows:
        return None
    row = rows[0]
    certificates = store().query_read(
        select(s.governed_model_certification)
        .where(s.governed_model_certification.c.revision_id == row["id"])
        .order_by(s.governed_model_certification.c.created_at.desc())
        .limit(1)
    )
    row["certification"] = certificates[0] if certificates else None
    return row


def detail(model_id: str) -> dict[str, Any] | None:
    model = get_model(model_id)
    if model is None:
        return None
    model["revisions"] = [
        get_revision(model_id, n) for n in range(model["latest_revision"], 0, -1)
    ]
    return model


def _insert_revision(
    model_id: str,
    revision: int,
    payload: dict[str, Any],
    fingerprint: str,
    user_id: str,
) -> None:
    store().query_write(
        s.governed_model_revision.insert().values(
            model_id=model_id,
            revision=revision,
            payload=payload,
            fingerprint=fingerprint,
            created_by=user_id,
        )
    )


def create_model(
    *, payload: dict[str, Any], fingerprint: str, user_id: str, owner_id: str | None
) -> dict[str, Any]:
    try:
        with write_transaction():
            store().query_write(
                s.governed_model.insert().values(
                    model_id=payload["model_id"],
                    name=payload["name"],
                    owner_id=owner_id,
                    created_by=user_id,
                )
            )
            _insert_revision(payload["model_id"], 1, payload, fingerprint, user_id)
    except IntegrityError as exc:
        raise VersionConflict(
            "Model ID already exists; create a draft using its current version"
        ) from exc
    return detail(payload["model_id"])


@contextmanager
def locked_model(model_id: str, expected_version: int) -> Iterator[dict[str, Any]]:
    """Lock before the compare so concurrent writes never both win."""
    with write_transaction():
        rows = store().query_read(
            select(s.governed_model)
            .where(s.governed_model.c.model_id == model_id)
            .with_for_update()
        )
        if not rows or rows[0]["archived"]:
            raise ModelNotFound(model_id)
        model = rows[0]
        if model["version"] != expected_version:
            raise VersionConflict(
                f"Expected version {expected_version}, current version is {model['version']}"
            )
        yield model
        store().query_write(
            update(s.governed_model)
            .where(s.governed_model.c.model_id == model_id)
            .values(version=model["version"] + 1)
        )


def create_draft(
    *,
    model_id: str,
    expected_version: int,
    payload: dict[str, Any],
    fingerprint: str,
    user_id: str,
) -> dict[str, Any]:
    with locked_model(model_id, expected_version) as model:
        revision = model["latest_revision"] + 1
        _insert_revision(model_id, revision, payload, fingerprint, user_id)
        store().query_write(
            update(s.governed_model)
            .where(s.governed_model.c.model_id == model_id)
            .values(latest_revision=revision, name=payload["name"])
        )
    return detail(model_id)


def set_revision_status(
    revision_id: str, status: str, validation: dict[str, Any] | None = None
) -> None:
    """Called only inside locked_model; never modifies the semantic payload."""
    values: dict[str, Any] = {"status": status}
    if validation is not None:
        values["validation"] = validation
    store().query_write(
        update(s.governed_model_revision)
        .where(s.governed_model_revision.c.id == revision_id)
        .values(**values)
    )


def certify(
    revision_id: str,
    *,
    reviewer_id: str,
    evidence: str,
    model_fingerprint: str,
    catalog_fingerprint: str,
) -> None:
    store().query_write(
        s.governed_model_certification.insert().values(
            revision_id=revision_id,
            reviewer_id=reviewer_id,
            evidence=evidence,
            model_fingerprint=model_fingerprint,
            catalog_fingerprint=catalog_fingerprint,
        )
    )
    set_revision_status(revision_id, "certified")


def set_active(model_id: str, revision: int | None) -> None:
    store().query_write(
        update(s.governed_model)
        .where(s.governed_model.c.model_id == model_id)
        .values(active_revision=revision)
    )


def archive(model_id: str, expected_version: int) -> dict[str, Any]:
    with locked_model(model_id, expected_version):
        store().query_write(
            update(s.governed_model)
            .where(s.governed_model.c.model_id == model_id)
            .values(archived=True, active_revision=None)
        )
    return detail(model_id)


@contextmanager
def read_snapshot(model_id: str) -> Iterator[None]:
    """Pin lifecycle state while a standard query is being executed."""
    with write_transaction():
        rows = store().query_read(
            select(s.governed_model)
            .where(s.governed_model.c.model_id == model_id)
            .with_for_update(read=True)
        )
        if not rows or rows[0]["archived"]:
            raise ModelNotFound(model_id)
        yield
