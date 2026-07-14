from __future__ import annotations

import base64
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field

from .agent import run_agent_cycle
from .config import load_settings
from .db import initialize_database, storage_name
from .event_bus import queue_status
from .import_service import import_plan
from .importer import inspect_excel
from .quality import dashboard
from .repositories import list_review_tasks
from .review_service import submit_review_and_update_agent
from .search import load_search_index, predict


@asynccontextmanager
async def lifespan(_: FastAPI):
    initialize_database()
    yield


app = FastAPI(title="РЭС AI API", version="1.0.0", lifespan=lifespan)


class PredictRequest(BaseModel):
    text: str = Field(min_length=1, max_length=5000)


class ReviewRequest(BaseModel):
    reviewer: str = Field(min_length=2, max_length=160)
    selected_res: list[str] = Field(default_factory=list)
    locality: str = ""
    district: str = ""
    settlement: str = ""
    street: str = ""
    none_correct: bool = False
    as_admin: bool = False


class ExcelImportRequest(BaseModel):
    file_name: str = Field(min_length=1, max_length=255)
    content_base64: str = Field(min_length=1)
    actor: str = "API"


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
    }


@app.post("/predict")
@app.post("/v1/predict")
def predict_endpoint(request: PredictRequest) -> dict[str, Any]:
    return _prediction_response(request.text)


@app.get("/v1/tasks")
def tasks_endpoint(
    reviewer: str = Query(min_length=2, max_length=160),
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    return {"items": list_review_tasks(reviewer, limit), "limit": limit}


@app.post("/v1/tasks/{task_id}/vote")
def vote_endpoint(
    task_id: int,
    request: ReviewRequest,
    x_admin_password: str | None = Header(default=None),
) -> dict[str, Any]:
    if request.as_admin:
        _require_admin(x_admin_password)
    selection = {
        "selected_res": [] if request.none_correct else list(dict.fromkeys(request.selected_res)),
        "locality": request.locality,
        "district": request.district,
        "settlement": request.settlement,
        "street": request.street,
    }
    if not selection["selected_res"] and not request.none_correct:
        raise HTTPException(status_code=422, detail="Выберите РЭС или укажите, что правильного варианта нет.")
    try:
        return submit_review_and_update_agent(
            task_id,
            request.reviewer,
            selection,
            request.as_admin,
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
        return import_plan(plan, actor=request.actor)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


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
