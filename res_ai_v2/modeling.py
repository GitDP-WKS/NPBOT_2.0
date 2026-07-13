from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import insert, select, update

from .db import get_engine, initialize_database, set_setting, utcnow
from .model_data import dump_model, load_model, predict_options
from .model_training import compute_candidate, quality_gate
from .normalize import normalize_text, stable_json
from .repositories import audit, create_or_update_task
from .schema import model_versions
from .structure import CURRENT_STRUCTURE

_CACHE: tuple[int, Any] | None = None
ALGORITHM = "tfidf-word-char-logreg-v2"


def _published_metrics(conn) -> dict[str, Any] | None:
    row = conn.execute(select(model_versions.c.metrics_json).where(model_versions.c.status == "published").order_by(model_versions.c.id.desc()).limit(1)).first()
    return json.loads(str(row.metrics_json)) if row else None


def train_candidate(actor: str = "Администратор") -> dict[str, Any]:
    result = compute_candidate(); metrics, version = result["metrics"], result["version"]
    with get_engine().begin() as conn:
        passed, reasons = quality_gate(metrics, _published_metrics(conn)); metrics["gate_reasons"] = reasons
        row = conn.execute(select(model_versions.c.id).where(model_versions.c.version == version)).first()
        values = dict(status="candidate", algorithm=ALGORITHM, training_signature=result["signature"], metrics_json=stable_json(metrics), confusion_json=stable_json(result["confusion"]), model_blob=dump_model(result["model"]), gate_passed=passed, created_at=utcnow(), published_at=None)
        if row: conn.execute(update(model_versions).where(model_versions.c.id == row.id).values(**values))
        else: conn.execute(insert(model_versions).values(version=version, **values))
    for error in result["errors"]:
        row = error["row"]; predicted = error["predicted"]; options = [{"res": row.label, "branch": CURRENT_STRUCTURE[row.label]}, {"res": predicted, "branch": CURRENT_STRUCTURE.get(predicted, "")}]
        create_or_update_task(task_key=hashlib.sha256(f"model|{version}|{row.group}|{row.label}".encode()).hexdigest(), task_type="model_error", subject_type="training_row", subject_key=row.group, title="Модель выбрала другой РЭС", payload={"query_text": row.text, "address": {}, "options": options, "expected_res": row.label, "predicted_res": predicted, "allow_multiple": False, "allow_address_edit": True}, priority=70)
    audit(actor, "train_candidate", "model", version, {}, metrics)
    return {"version": version, "metrics": metrics, "gate_passed": passed, "gate_reasons": reasons, "confusion": result["confusion"]}


def list_model_versions(limit: int = 30) -> list[dict[str, Any]]:
    initialize_database()
    with get_engine().connect() as conn: rows = conn.execute(select(model_versions).order_by(model_versions.c.id.desc()).limit(limit)).all()
    return [{**{k: v for k, v in dict(row._mapping).items() if k != "model_blob"}, "metrics": json.loads(str(row.metrics_json)), "confusion": json.loads(str(row.confusion_json))} for row in rows]


def publish_candidate(version: str, actor: str = "Администратор", force: bool = False) -> None:
    global _CACHE
    with get_engine().begin() as conn:
        row = conn.execute(select(model_versions).where(model_versions.c.version == version)).first()
        if not row: raise ValueError("Версия модели не найдена.")
        if not bool(row.gate_passed) and not force: raise ValueError("Кандидат не прошел порог качества.")
        conn.execute(update(model_versions).where(model_versions.c.status == "published").values(status="archived")); conn.execute(update(model_versions).where(model_versions.c.version == version).values(status="published", published_at=utcnow()))
    _CACHE = None; set_setting("human_decisions_since_training", "0"); audit(actor, "publish_model", "model", version)


def rollback_model(version: str, actor: str = "Администратор") -> None:
    global _CACHE
    with get_engine().begin() as conn:
        if not conn.execute(select(model_versions.c.id).where(model_versions.c.version == version)).first(): raise ValueError("Версия модели не найдена.")
        conn.execute(update(model_versions).where(model_versions.c.status == "published").values(status="archived")); conn.execute(update(model_versions).where(model_versions.c.version == version).values(status="published", published_at=utcnow()))
    _CACHE = None; audit(actor, "rollback_model", "model", version)


def published_model_info() -> dict[str, Any] | None:
    versions = [row for row in list_model_versions(10) if row["status"] == "published"]; return versions[0] if versions else None


def _load_published() -> tuple[Any, str] | None:
    global _CACHE
    with get_engine().connect() as conn: row = conn.execute(select(model_versions.c.id, model_versions.c.version, model_versions.c.model_blob).where(model_versions.c.status == "published").order_by(model_versions.c.id.desc()).limit(1)).first()
    if not row: return None
    if _CACHE and _CACHE[0] == int(row.id): return _CACHE[1], str(row.version)
    model = load_model(bytes(row.model_blob)); _CACHE = (int(row.id), model); return model, str(row.version)


def predict_with_model(text: str, top_n: int = 3) -> tuple[list[dict[str, Any]], str | None]:
    loaded = _load_published()
    if not loaded: return [], None
    model, version = loaded; options = predict_options(model, normalize_text(text), top_n)
    for item in options: item["branch"] = CURRENT_STRUCTURE.get(item["res"], "")
    return options, version
