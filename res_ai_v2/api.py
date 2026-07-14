from __future__ import annotations

import base64
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field

from .admin_service import request_full_analysis
from .agent import run_agent_cycle
from .background_worker import start_background_worker
from .config import load_settings
from .daily_audit import ensure_daily_audit
from .db import initialize_database, storage_name
from .diagnostics import run_diagnostics
from .event_bus import queue_status
from .explanations import active_conditional_rules, mapping_explanation, recent_recalculations
from .import_service import import_plan
from .importer import inspect_excel
from .modeling import compare_model_versions, list_model_versions, rollback_model
from .quality import dashboard
from .review_queue import claim_review_task
from .review_service import submit_review_and_update_agent
from .search import load_search_index, predict


@asynccontextmanager
async def lifespan(_: FastAPI):
    initialize_database()
    ensure_daily_audit()
    start_background_worker()
    yield


app = FastAPI(title="РЭС AI API", version="1.3.0", lifespan=lifespan)


class PredictRequest(BaseModel):
    text: str = Field(min_length=1, max_length=5000)


class ReviewCondition(BaseModel):
    res: str = Field(min_length=1, max_length=255)
    ambiguity_key: str = ""
    condition: dict[str, Any] = Field(default_factory=dict)


class ReviewRequest(BaseModel):
    reviewer: str = Field(min_length=2, max_length=160)
    lease_token: str = Field(min_length=16, max_length=128)
    decision_type: str = "confirmed"
    selected_res: list[str] = Field(default_factory=list)
    conditions: list[ReviewCondition] = Field(default_factory=list)
    locality: str = ""
    district: str = ""
    settlement: str = ""
    street: str = ""
    house: str = ""
    comment: str = ""
    none_correct: bool = False
    as_admin: bool = False


class ExcelImportRequest(BaseModel):
    file_name: str = Field(min_length=1, max_length=255)
    content_base64: str = Field(min_length=1)
    actor: str = "API"


class AdminActionRequest(BaseModel):
    actor: str = "Администратор API"


class RollbackRequest(BaseModel):
    version: str = Field(min_length=1, max_length=64)
    actor: str = "Администратор API"


def _prediction_response(text: str) -> dict[str, Any]:
    result = predict(text, index=load_search_index(), enqueue=True)
    return {
        "status": result.status,
        "confidence": result.confidence,
        "method": result.method,
        "reason": result.reason,
        "needs_review": result.needs_review,
        "model_version": result.model_version,
        "candidates": result.candidates,
    }


def _require_admin(password: str | None) -> None:
    if not password or password != load_settings().admin_password:
        raise HTTPException(status_code=403, detail="Неверный пароль администратора.")


@app.get("/health")
@app.get("/v1/health")
def health() -> dict[str, str]:
    return {"status": "ok", "storage": storage_name()}


@app.get("/v1/system/status")
def system_status() -> dict[str, Any]:
    return {
        "storage": storage_name(),
        "database": dashboard(),
        "event_queue": queue_status(),
        "diagnostics": run_diagnostics(),
    }


@app.post("/predict")
@app.post("/v1/predict")
def predict_endpoint(request: PredictRequest) -> dict[str, Any]:
    return _prediction_response(request.text)


@app.get("/v1/tasks")
def tasks_endpoint(
    reviewer: str = Query(min_length=2, max_length=160),
) -> dict[str, Any]:
    try:
        task = claim_review_task(reviewer)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"items": [task] if task else [], "limit": 1}


@app.post("/v1/tasks/{task_id}/vote")
def vote_endpoint(
    task_id: int,
    request: ReviewRequest,
    x_admin_password: str | None = Header(default=None),
) -> dict[str, Any]:
    if request.as_admin:
        _require_admin(x_admin_password)
    decision_type = "source_error" if request.none_correct else request.decision_type
    selection = {
        "decision_type": decision_type,
        "selected_res": []
        if request.none_correct
        else list(dict.fromkeys(request.selected_res)),
        "conditions": [item.model_dump() for item in request.conditions],
        "locality": request.locality,
        "district": request.district,
        "settlement": request.settlement,
        "street": request.street,
        "house": request.house,
        "comment": request.comment,
    }
    try:
        return submit_review_and_update_agent(
            task_id,
            request.reviewer,
            selection,
            request.as_admin,
            request.lease_token,
            wait_for_agent=False,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/v1/files/excel")
def import_excel_endpoint(
    request: ExcelImportRequest,
    x_admin_password: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_admin(x_admin_password)
    try:
        content = base64.b64decode(request.content_base64, validate=True)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Некорректное содержимое файла.") from exc
    try:
        plan = inspect_excel(content, request.file_name)
        return import_plan(plan, actor=request.actor, wait_for_agent=False)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/v1/mappings/{mapping_id}/explanation")
def mapping_explanation_endpoint(mapping_id: int) -> dict[str, Any]:
    result = mapping_explanation(mapping_id)
    if not result:
        raise HTTPException(status_code=404, detail="Связь адреса с РЭС не найдена.")
    return result


@app.get("/v1/rules")
def conditional_rules_endpoint(
    ambiguity_key: str = "",
    limit: int = Query(default=500, ge=1, le=2000),
) -> dict[str, Any]:
    return {"items": active_conditional_rules(ambiguity_key, limit)}


@app.get("/v1/recalculations")
def recalculations_endpoint(
    limit: int = Query(default=100, ge=1, le=1000),
) -> dict[str, Any]:
    return {"items": recent_recalculations(limit)}


@app.get("/v1/models")
def models_endpoint(limit: int = Query(default=30, ge=1, le=200)) -> dict[str, Any]:
    return {"items": list_model_versions(limit)}


@app.get("/v1/models/compare")
def compare_models_endpoint(left: str, right: str) -> dict[str, Any]:
    try:
        return compare_model_versions(left, right)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/v1/admin/analysis/full")
def full_analysis_endpoint(
    request: AdminActionRequest,
    x_admin_password: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_admin(x_admin_password)
    return request_full_analysis(request.actor, wait_for_agent=False)


@app.post("/v1/admin/models/rollback")
def rollback_model_endpoint(
    request: RollbackRequest,
    x_admin_password: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_admin(x_admin_password)
    try:
        rollback_model(request.version, request.actor)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"status": "rolled_back", "version": request.version}


@app.post("/v1/admin/agent/process")
def process_agent_endpoint(
    max_events: int = Query(default=50, ge=1, le=500),
    x_admin_password: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_admin(x_admin_password)
    result = run_agent_cycle(max_events=max_events, worker_id="api-admin")
    return {
        "processed": result.processed,
        "completed": result.completed,
        "failed": result.failed,
        "results": result.results,
    }
