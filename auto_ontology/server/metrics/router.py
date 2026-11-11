# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Private-backend endpoints for the authenticated governed-metrics gateway."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field

from auto_ontology.connectors.base import StatementTimeout
from auto_ontology.dal import governed_models as dal
from auto_ontology.dal.owners import CATALOG_EDIT, EditSubject
from auto_ontology.semantic.contracts.models import (
    ContractError,
    GovernedModel,
    MetricQueryRequest,
)
from auto_ontology.server.identity import resolve_internal_user
from auto_ontology.server.metrics import service
from auto_ontology.server.metrics.interpretation import (
    InterpretRequest,
    interpret_question,
)
from auto_ontology.server.owners import can_edit_entity, owner_from_body
from auto_ontology.server.users.permissions import permissions_for_role
from auto_ontology.server.users.postgres_dal import get_user_role


class MetricsRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def handle(request: Request) -> Response:
            try:
                return await original(request)
            except dal.ModelNotFound as exc:
                raise HTTPException(
                    404, detail={"code": "MODEL_NOT_FOUND", "message": str(exc)}
                ) from exc
            except dal.VersionConflict as exc:
                raise HTTPException(
                    409, detail={"code": "VERSION_CONFLICT", "message": str(exc)}
                ) from exc
            except ContractError as exc:
                code = 503 if exc.code == "FEATURE_DISABLED" else 422
                if exc.code in {
                    "NOT_ACTIVE",
                    "NOT_CERTIFIED",
                    "SOURCE_CHANGED",
                    "REVISION_CHANGED",
                    "REVISION_DEPRECATED",
                    "INVALID_TRANSITION",
                }:
                    code = 409
                raise HTTPException(
                    code,
                    detail={"code": exc.code, "message": exc.message, "path": exc.path},
                ) from exc
            except StatementTimeout as exc:
                raise HTTPException(
                    504,
                    detail={
                        "code": "QUERY_TIMEOUT",
                        "message": "The governed query exceeded its time limit",
                    },
                ) from exc
            except psycopg.Error as exc:
                # Database exception text can contain SQL and connection details.
                raise HTTPException(
                    502,
                    detail={
                        "code": "SOURCE_QUERY_FAILED",
                        "message": "PostgreSQL could not validate or execute this query",
                    },
                ) from exc

        return handle


def reader(request: Request) -> str:
    user_id = resolve_internal_user(request, required=True)
    assert user_id is not None
    if "catalog:read" not in permissions_for_role(get_user_role(user_id) or ""):
        raise HTTPException(403, detail="Catalog read permission required")
    return user_id


def editor(user_id: str, model_id: str | None = None) -> None:
    owner_id = None
    if model_id:
        row = dal.get_model(model_id)
        if row is None or row["archived"]:
            raise dal.ModelNotFound(model_id)
        owner_id = row["owner_id"]
    if not can_edit_entity(
        user_id=user_id, subject=EditSubject(permission=CATALOG_EDIT, owner_id=owner_id)
    ):
        raise HTTPException(403, detail="Catalog edit permission required")


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateRequest(Body):
    model: GovernedModel
    owner_id: str | None = None


class ImportRequest(CreateRequest):
    format: str = "auto-ontology-governed-model-v1"
    provenance: dict[str, Any] | None = None


class DraftRequest(Body):
    model: GovernedModel
    expected_version: int = Field(ge=1)


class TransitionRequest(Body):
    revision: int = Field(ge=1)
    expected_version: int = Field(ge=1)
    evidence: str | None = Field(default=None, max_length=20000)


class QueryRequest(Body):
    query: MetricQueryRequest
    revision: int | None = Field(default=None, ge=1)
    preview: bool = False


class InterpretationRequest(InterpretRequest):
    revision: int | None = Field(default=None, ge=1)


router = APIRouter(prefix="/metrics", route_class=MetricsRoute)


@router.get("/config")
def config(user_id: str = Depends(reader)) -> dict[str, Any]:
    """Report the query rollout flag; model governance remains available."""
    return {"data": {"enabled": service.enabled()}}


@router.get("/models")
def list_models(user_id: str = Depends(reader)) -> dict[str, Any]:
    """List non-archived governed models visible to catalog readers."""
    return {"data": dal.list_models()}


@router.post("/models", status_code=201)
def create(body: CreateRequest, user_id: str = Depends(reader)) -> dict[str, Any]:
    """Create an untrusted draft bound to real Catalog and Term IDs."""
    editor(user_id)
    return {
        "data": service.create(
            body.model,
            user_id=user_id,
            owner_id=owner_from_body(body.owner_id, CATALOG_EDIT),
        )
    }


@router.post("/import", status_code=201)
def import_model(body: ImportRequest, user_id: str = Depends(reader)) -> dict[str, Any]:
    """Import a native package as a new draft; never inherit certification."""
    editor(user_id)
    if body.format != "auto-ontology-governed-model-v1":
        raise ContractError(
            "UNSUPPORTED_FORMAT",
            "Only the native governed model v1 package is supported",
        )
    return {
        "data": service.create(
            body.model,
            user_id=user_id,
            owner_id=owner_from_body(body.owner_id, CATALOG_EDIT),
        )
    }


@router.get("/models/{model_id}")
def detail(model_id: str, user_id: str = Depends(reader)) -> dict[str, Any]:
    """Read immutable revisions and server-recorded certification evidence."""
    row = dal.detail(model_id)
    if row is None:
        raise dal.ModelNotFound(model_id)
    return {"data": row}


@router.put("/models/{model_id}/draft")
def draft(
    model_id: str, body: DraftRequest, user_id: str = Depends(reader)
) -> dict[str, Any]:
    """Append a draft without changing the active certified revision."""
    editor(user_id, model_id)
    return {
        "data": service.draft(
            model_id,
            body.model,
            expected_version=body.expected_version,
            user_id=user_id,
        )
    }


@router.post("/models/{model_id}/validate")
def validate(
    model_id: str, body: TransitionRequest, user_id: str = Depends(reader)
) -> dict[str, Any]:
    """Validate contracts, actual Catalog bindings, grain and join guarantees."""
    editor(user_id, model_id)
    return {
        "data": service.transition(
            model_id, action="validate", user_id=user_id, **body.model_dump()
        )
    }


@router.post("/models/{model_id}/certify")
def certify(
    model_id: str, body: TransitionRequest, user_id: str = Depends(reader)
) -> dict[str, Any]:
    """Record authenticated review evidence and a live PostgreSQL fingerprint."""
    editor(user_id, model_id)
    return {
        "data": service.transition(
            model_id, action="certify", user_id=user_id, **body.model_dump()
        )
    }


@router.post("/models/{model_id}/activate")
def activate(
    model_id: str, body: TransitionRequest, user_id: str = Depends(reader)
) -> dict[str, Any]:
    """Activate a certified revision only while its reviewed sources still match."""
    editor(user_id, model_id)
    return {
        "data": service.transition(
            model_id, action="activate", user_id=user_id, **body.model_dump()
        )
    }


@router.post("/models/{model_id}/deprecate")
def deprecate(
    model_id: str, body: TransitionRequest, user_id: str = Depends(reader)
) -> dict[str, Any]:
    """Deprecate a revision and revoke its active pointer atomically."""
    editor(user_id, model_id)
    return {
        "data": service.transition(
            model_id, action="deprecate", user_id=user_id, **body.model_dump()
        )
    }


@router.delete("/models/{model_id}")
def archive(
    model_id: str, expected_version: int = Query(ge=1), user_id: str = Depends(reader)
) -> dict[str, Any]:
    """Archive a model while retaining all revisions and review evidence."""
    editor(user_id, model_id)
    return {"data": dal.archive(model_id, expected_version)}


@router.get("/models/{model_id}/export")
def export(
    model_id: str,
    revision: int | None = Query(default=None, ge=1),
    user_id: str = Depends(reader),
) -> dict[str, Any]:
    """Export a native contract snapshot with informational provenance."""
    parent = dal.get_model(model_id)
    if parent is None:
        raise dal.ModelNotFound(model_id)
    row = dal.get_revision(model_id, revision or parent["latest_revision"])
    if row is None:
        raise dal.ModelNotFound(model_id)
    return {
        "data": {
            "format": "auto-ontology-governed-model-v1",
            "model": row["payload"],
            "provenance": {
                key: row[key]
                for key in ("revision", "fingerprint", "status", "certification")
            },
        }
    }


@router.post("/models/{model_id}/compile")
def compile_model(
    model_id: str, body: QueryRequest, user_id: str = Depends(reader)
) -> dict[str, Any]:
    """Compile a standard query, or an explicitly untrusted editor preview."""
    if body.preview:
        editor(user_id, model_id)
    return {
        "data": service.compile_model(
            model_id, body.query, revision=body.revision, preview=body.preview
        )
    }


@router.post("/models/{model_id}/execute")
def execute(
    model_id: str, body: QueryRequest, user_id: str = Depends(reader)
) -> dict[str, Any]:
    """Revalidate and execute server-compiled SQL using bound PostgreSQL parameters."""
    if body.preview:
        raise ContractError(
            "PREVIEW_NOT_EXECUTABLE",
            "Draft previews cannot execute through the standard query endpoint",
        )
    return {"data": service.execute(model_id, body.query, revision=body.revision)}


@router.post("/models/{model_id}/interpret")
def interpret(
    model_id: str, body: InterpretationRequest, user_id: str = Depends(reader)
) -> dict[str, Any]:
    """Interpret a question against the active certified model and validate its plan."""
    model, _, _, _ = service.executable_model(model_id, body.revision)
    result = interpret_question(
        model,
        InterpretRequest(
            question=body.question, selected_metric_ids=body.selected_metric_ids
        ),
    )
    return {"data": result.model_dump(mode="json")}
