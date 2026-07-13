from __future__ import annotations

import json
from typing import Any

from sqlalchemy import func, select

from .db import get_engine, initialize_database
from .modeling import list_model_versions, published_model_info
from .repositories import stats
from .schema import address_mappings, model_versions, prediction_events, review_tasks, source_files, text_examples


def dashboard() -> dict[str, Any]:
    values = stats(); published = published_model_info()
    values.update({"model_status": "Опубликована" if published else "Не обучена", "model_accuracy": round(float(published["metrics"].get("accuracy", 0.0)) * 100, 2) if published else None, "model_version": published["version"] if published else None})
    return values


def quality_details() -> dict[str, Any]:
    initialize_database()
    with get_engine().connect() as conn:
        mapping_status = {str(row.status): int(row.count) for row in conn.execute(select(address_mappings.c.status, func.count().label("count")).where(address_mappings.c.active.is_(True)).group_by(address_mappings.c.status))}
        open_by_type = {str(row.task_type): int(row.count) for row in conn.execute(select(review_tasks.c.task_type, func.count().label("count")).where(review_tasks.c.status == "open").group_by(review_tasks.c.task_type))}
        prediction_count = int(conn.scalar(select(func.count()).select_from(prediction_events)) or 0)
        text_count = int(conn.scalar(select(func.count()).select_from(text_examples)) or 0)
        file_count = int(conn.scalar(select(func.count()).select_from(source_files)) or 0)
    return {"mapping_status": mapping_status, "open_by_type": open_by_type, "prediction_count": prediction_count, "text_count": text_count, "file_count": file_count, "models": list_model_versions()}


def model_confusion(version: str) -> list[dict[str, Any]]:
    initialize_database()
    with get_engine().connect() as conn: row = conn.execute(select(model_versions.c.confusion_json).where(model_versions.c.version == version)).first()
    return json.loads(str(row.confusion_json)) if row else []
