from __future__ import annotations

import base64
import json
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, LargeBinary, delete, insert, select, text, update

from .db import get_engine, utcnow
from .domain_schema import (
    canonical_observations,
    conditional_rules,
    evidence_claims,
    source_evidence,
    source_quality_history,
    source_registries,
)
from .normalize import stable_json
from .pit_schema import knowledge_directives, pit_observations, pit_occurrences, review_task_leases
from .restore_schema import restore_requests
from .schema import (
    model_versions,
    query_rules,
    review_decisions,
    review_tasks,
    review_votes,
    source_files,
    source_rows,
)

RESTORE_TABLES = {
    table.name.removeprefix("res_ai_v2_"): table
    for table in (
        source_files,
        source_rows,
        pit_observations,
        pit_occurrences,
        canonical_observations,
        source_registries,
        evidence_claims,
        source_evidence,
        review_tasks,
        review_decisions,
        knowledge_directives,
        query_rules,
        source_quality_history,
        model_versions,
        conditional_rules,
    )
}
DELETE_ORDER = (
    conditional_rules,
    knowledge_directives,
    review_task_leases,
    review_votes,
    review_decisions,
    source_evidence,
    evidence_claims,
    canonical_observations,
    pit_occurrences,
    query_rules,
    model_versions,
    review_tasks,
    source_rows,
    source_registries,
    pit_observations,
    source_files,
    source_quality_history,
)
INSERT_ORDER = (
    source_files,
    source_rows,
    pit_observations,
    pit_occurrences,
    canonical_observations,
    source_registries,
    evidence_claims,
    source_evidence,
    review_tasks,
    review_decisions,
    knowledge_directives,
    query_rules,
    source_quality_history,
    model_versions,
    conditional_rules,
)


def _decode_datetime(value: Any) -> Any:
    if value in (None, "") or isinstance(value, datetime):
        return value
    cleaned = str(value).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(cleaned)
    except ValueError:
        return value


def _decode_row(table, row: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for column in table.columns:
        if column.name not in row:
            continue
        value = row[column.name]
        if isinstance(column.type, DateTime):
            value = _decode_datetime(value)
        elif isinstance(column.type, LargeBinary) and isinstance(value, str):
            value = base64.b64decode(value.encode("ascii"))
        result[column.name] = value
    if table is conditional_rules:
        result["generation_id"] = None
    return result


def _reset_postgresql_sequences(conn) -> None:
    if conn.dialect.name != "postgresql":
        return
    for table in INSERT_ORDER:
        primary = [column for column in table.primary_key.columns if column.autoincrement]
        if len(primary) != 1:
            continue
        column = primary[0]
        conn.execute(
            text(
                "SELECT setval(pg_get_serial_sequence(:table_name, :column_name), "
                "COALESCE((SELECT MAX(" + column.name + ") FROM " + table.name + "), 1), true)"
            ),
            {"table_name": table.name, "column_name": column.name},
        )


def restore_snapshot_payload(snapshot_json: str) -> dict[str, int]:
    payload = json.loads(snapshot_json)
    if payload.get("format") not in {"res_ai_v3_full_backup", "res_ai_v3_backup"}:
        raise ValueError("Неподдерживаемый формат резервной копии.")
    tables = payload.get("tables")
    if not isinstance(tables, dict):
        raise ValueError("В резервной копии отсутствуют таблицы.")

    restored: dict[str, int] = {}
    with get_engine().begin() as conn:
        for table in DELETE_ORDER:
            conn.execute(delete(table))
        for table in INSERT_ORDER:
            short_name = table.name.removeprefix("res_ai_v2_")
            rows = tables.get(short_name, tables.get(table.name, []))
            if not isinstance(rows, list):
                raise ValueError(f"Некорректный раздел резервной копии: {short_name}")
            decoded = [_decode_row(table, dict(row)) for row in rows]
            decoded = [row for row in decoded if row]
            if decoded:
                conn.execute(insert(table), decoded)
            restored[short_name] = len(decoded)
        _reset_postgresql_sequences(conn)
    return restored


def apply_restore_request(request_id: int) -> dict[str, Any]:
    with get_engine().begin() as conn:
        request = conn.execute(
            select(restore_requests).where(restore_requests.c.id == request_id)
        ).first()
        if not request:
            raise ValueError("Запрос восстановления не найден.")
        conn.execute(
            update(restore_requests)
            .where(restore_requests.c.id == request_id)
            .values(status="processing", error_text="")
        )
        snapshot_json = str(request.snapshot_json)
    try:
        restored = restore_snapshot_payload(snapshot_json)
    except Exception as exc:
        with get_engine().begin() as conn:
            conn.execute(
                update(restore_requests)
                .where(restore_requests.c.id == request_id)
                .values(
                    status="failed",
                    error_text=str(exc)[:4000],
                    finished_at=utcnow(),
                )
            )
        raise
    result = {"request_id": request_id, "restored": restored}
    with get_engine().begin() as conn:
        conn.execute(
            update(restore_requests)
            .where(restore_requests.c.id == request_id)
            .values(
                status="restored",
                result_json=stable_json(result),
                finished_at=utcnow(),
            )
        )
    return result
