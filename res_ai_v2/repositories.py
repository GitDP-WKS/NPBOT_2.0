from __future__ import annotations

import base64
import json
from collections.abc import Iterable
from typing import Any

from sqlalchemy import and_, delete, func, insert, or_, select, update

from .db import bump_data_version, get_engine, increment_setting, initialize_database, utcnow
from .event_schema import agent_effects, agent_events, agent_runs
from .normalize import normalize_text, stable_json
from .schema import (
    addresses,
    address_mappings,
    audit_events,
    mapping_evidence,
    model_versions,
    prediction_events,
    query_rules,
    review_decisions,
    review_tasks,
    review_votes,
    source_files,
    source_rows,
    text_examples,
)


def audit(actor: str, action: str, entity_type: str, entity_key: str, before: Any = None, after: Any = None) -> None:
    initialize_database()
    with get_engine().begin() as conn:
        conn.execute(
            insert(audit_events).values(
                actor=actor or "system",
                action=action,
                entity_type=entity_type,
                entity_key=entity_key,
                before_json=stable_json(before or {}),
                after_json=stable_json(after or {}),
                created_at=utcnow(),
            )
        )


def create_or_update_task(*, task_key: str, task_type: str, subject_type: str, subject_key: str, title: str, payload: dict[str, Any], priority: int) -> int:
    initialize_database()
    now = utcnow()
    encoded = stable_json(payload)
    with get_engine().begin() as conn:
        row = conn.execute(select(review_tasks.c.id).where(review_tasks.c.task_key == task_key)).first()
        values = dict(
            task_type=task_type,
            subject_type=subject_type,
            subject_key=subject_key,
            title=title,
            payload_json=encoded,
            priority=priority,
            status="open",
            updated_at=now,
        )
        if row:
            conn.execute(update(review_tasks).where(review_tasks.c.id == row.id).values(**values))
            return int(row.id)
        return int(
            conn.execute(
                insert(review_tasks).values(task_key=task_key, created_at=now, **values)
            ).inserted_primary_key[0]
        )


def close_stale_tasks(task_types: Iterable[str], keep_keys: set[str]) -> int:
    with get_engine().begin() as conn:
        rows = conn.execute(
            select(review_tasks.c.id, review_tasks.c.task_key).where(
                review_tasks.c.status == "open",
                review_tasks.c.task_type.in_(list(task_types)),
            )
        ).all()
        stale = [int(row.id) for row in rows if str(row.task_key) not in keep_keys]
        if stale:
            conn.execute(
                update(review_tasks)
                .where(review_tasks.c.id.in_(stale))
                .values(status="cancelled", updated_at=utcnow())
            )
        return len(stale)


def list_review_tasks(reviewer: str, limit: int = 50) -> list[dict[str, Any]]:
    initialize_database()
    reviewer_key = normalize_text(reviewer)
    with get_engine().connect() as conn:
        reviewed = (
            set(conn.scalars(select(review_votes.c.task_id).where(review_votes.c.reviewer == reviewer_key)))
            if reviewer_key
            else set()
        )
        rows = conn.execute(
            select(review_tasks)
            .where(review_tasks.c.status == "open")
            .order_by(review_tasks.c.priority.desc(), review_tasks.c.created_at, review_tasks.c.id)
            .limit(max(100, limit * 3))
        ).all()
    result = []
    for row in rows:
        if int(row.id) in reviewed:
            continue
        item = dict(row._mapping)
        item["payload"] = json.loads(item.pop("payload_json") or "{}")
        result.append(item)
        if len(result) >= limit:
            break
    return result


def knowledge_rows(limit: int | None = None) -> list[dict[str, Any]]:
    query = (
        select(
            addresses.c.id.label("address_id"),
            addresses.c.address_key,
            addresses.c.locality,
            addresses.c.district,
            addresses.c.settlement,
            addresses.c.street,
            addresses.c.locality_key,
            addresses.c.district_key,
            addresses.c.settlement_key,
            addresses.c.street_key,
            address_mappings.c.id.label("mapping_id"),
            address_mappings.c.res_name,
            address_mappings.c.branch_name,
            address_mappings.c.status,
            address_mappings.c.source_confidence,
            address_mappings.c.human_confirmations,
            address_mappings.c.active,
        )
        .select_from(addresses.join(address_mappings, addresses.c.id == address_mappings.c.address_id))
        .where(address_mappings.c.active.is_(True))
        .order_by(address_mappings.c.branch_name, address_mappings.c.res_name, addresses.c.locality)
    )
    if limit:
        query = query.limit(limit)
    with get_engine().connect() as conn:
        return [dict(row._mapping) for row in conn.execute(query)]


def stats() -> dict[str, int]:
    initialize_database()
    with get_engine().connect() as conn:
        def count(table, *where):
            return int(conn.scalar(select(func.count()).select_from(table).where(*where)) or 0)

        return {
            "addresses": count(addresses),
            "mappings": count(address_mappings, address_mappings.c.active.is_(True)),
            "open_tasks": count(review_tasks, review_tasks.c.status == "open"),
            "conflicts": count(
                review_tasks,
                review_tasks.c.status == "open",
                review_tasks.c.task_type == "mapping_conflict",
            ),
            "human_verified": count(
                address_mappings,
                address_mappings.c.active.is_(True),
                address_mappings.c.status == "human_verified",
            ),
            "text_examples": count(text_examples),
            "source_files": count(source_files),
        }


def increment_human_decisions() -> int:
    return increment_setting("human_decisions_since_training", 1, default=0)


def save_query_rule(raw_query: str, selection: list[dict[str, Any]], actor: str) -> None:
    normalized, now, encoded = normalize_text(raw_query), utcnow(), stable_json(selection)
    with get_engine().begin() as conn:
        row = conn.execute(select(query_rules.c.id).where(query_rules.c.normalized_query == normalized)).first()
        values = dict(raw_query=raw_query, selection_json=encoded, created_by=actor, updated_at=now)
        if row:
            conn.execute(update(query_rules).where(query_rules.c.id == row.id).values(**values))
        else:
            conn.execute(insert(query_rules).values(normalized_query=normalized, created_at=now, **values))


def lookup_query_rule(raw_query: str) -> list[dict[str, Any]] | None:
    normalized = normalize_text(raw_query)
    if not normalized:
        return None
    with get_engine().connect() as conn:
        row = conn.execute(
            select(query_rules.c.selection_json).where(query_rules.c.normalized_query == normalized)
        ).first()
    return json.loads(str(row.selection_json) or "[]") if row else None


def delete_query_rule(raw_query: str) -> None:
    with get_engine().begin() as conn:
        conn.execute(delete(query_rules).where(query_rules.c.normalized_query == normalize_text(raw_query)))


def recent_audit(limit: int = 200) -> list[dict[str, Any]]:
    with get_engine().connect() as conn:
        return [
            dict(row._mapping)
            for row in conn.execute(select(audit_events).order_by(audit_events.c.id.desc()).limit(limit))
        ]


def browse_knowledge(search_text: str = "", status: str = "", branch: str = "", res_name: str = "", limit: int = 500) -> list[dict[str, Any]]:
    query = (
        select(
            address_mappings.c.id.label("mapping_id"),
            address_mappings.c.branch_name,
            address_mappings.c.res_name,
            addresses.c.locality,
            addresses.c.district,
            addresses.c.settlement,
            addresses.c.street,
            address_mappings.c.status,
            address_mappings.c.source_confidence,
            address_mappings.c.human_confirmations,
            address_mappings.c.active,
        )
        .select_from(addresses.join(address_mappings, addresses.c.id == address_mappings.c.address_id))
        .where(address_mappings.c.active.is_(True))
        .order_by(address_mappings.c.branch_name, address_mappings.c.res_name, addresses.c.locality)
        .limit(limit)
    )
    conditions = []
    if status:
        conditions.append(address_mappings.c.status == status)
    if branch:
        conditions.append(address_mappings.c.branch_name == branch)
    if res_name:
        conditions.append(address_mappings.c.res_name == res_name)
    if search_text.strip():
        pattern = f"%{search_text.strip()}%"
        conditions.append(
            or_(
                addresses.c.locality.ilike(pattern),
                addresses.c.district.ilike(pattern),
                addresses.c.settlement.ilike(pattern),
                addresses.c.street.ilike(pattern),
            )
        )
    if conditions:
        query = query.where(and_(*conditions))
    with get_engine().connect() as conn:
        return [dict(row._mapping) for row in conn.execute(query)]


def backup_snapshot() -> str:
    from . import schema as s

    tables = [
        source_files,
        source_rows,
        addresses,
        address_mappings,
        mapping_evidence,
        text_examples,
        query_rules,
        review_tasks,
        review_votes,
        review_decisions,
        model_versions,
        prediction_events,
        audit_events,
        agent_events,
        agent_runs,
        agent_effects,
    ]
    data: dict[str, Any] = {"format": "res_ai_v2_backup", "tables": {}}
    with get_engine().connect() as conn:
        for table in tables:
            rows = [dict(row._mapping) for row in conn.execute(select(table))]
            if table is model_versions:
                for row in rows:
                    if row.get("model_blob") is not None:
                        row["model_blob"] = base64.b64encode(bytes(row["model_blob"])).decode("ascii")
            data["tables"][table.name.removeprefix(s.PREFIX)] = rows
    return json.dumps(data, ensure_ascii=False, indent=2, default=str)


def changed() -> None:
    bump_data_version()
