from __future__ import annotations

import json
from typing import Any

from sqlalchemy import delete, insert, select, update
from sqlalchemy.exc import IntegrityError

from .db import bump_data_version, get_engine, get_setting, initialize_database, utcnow
from .event_bus import publish_event
from .normalize import normalize_text, sha256_parts, stable_json
from .pit_schema import knowledge_directives
from .repositories import audit, increment_human_decisions
from .schema import review_decisions, review_tasks, review_votes


def _evidence_signature(task: dict[str, Any]) -> str:
    payload = json.loads(str(task.get("payload_json", "{}")) or "{}")
    options = [
        {
            "res": str(option.get("res", "")),
            "district": str(option.get("district", "")),
            "occurrences": int(option.get("occurrences", 0) or 0),
        }
        for option in payload.get("options", [])
    ]
    options.sort(key=lambda item: (item["res"], item["district"], item["occurrences"]))
    return sha256_parts(
        [
            str(task.get("task_type", "")),
            str(task.get("subject_key", "")),
            stable_json(payload.get("address") or {}),
            stable_json(options),
            str(payload.get("raw_text", "")),
        ]
    )


def submit_review(
    task_id: int,
    reviewer: str,
    selection: dict[str, Any],
    is_admin: bool,
) -> dict[str, Any]:
    """Сохраняет одно решение как директиву агенту."""
    actor = " ".join(reviewer.strip().split())
    reviewer_key = normalize_text(reviewer)
    if not reviewer_key:
        raise ValueError("Укажите имя проверяющего.")
    initialize_database()
    encoded = stable_json(selection)
    task_title = ""
    task_type = ""
    decision_id = 0
    directive_id = 0
    directive_version = int(get_setting("data_version", "1")) + 2

    with get_engine().begin() as conn:
        row = conn.execute(
            select(review_tasks).where(
                review_tasks.c.id == task_id,
                review_tasks.c.status == "open",
            )
        ).first()
        if not row:
            return {
                "applied": False,
                "votes": 0,
                "required": 1,
                "message": "Задание уже закрыто.",
            }
        task = dict(row._mapping)
        task_title = str(task.get("title", ""))
        task_type = str(task.get("task_type", ""))
        evidence_signature = _evidence_signature(task)
        try:
            conn.execute(
                insert(review_votes).values(
                    task_id=task_id,
                    reviewer=reviewer_key,
                    selection_json=encoded,
                    is_admin=is_admin,
                    created_at=utcnow(),
                )
            )
        except IntegrityError as exc:
            raise ValueError("Вы уже проверяли это задание.") from exc

        now = utcnow()
        conn.execute(
            update(review_tasks)
            .where(review_tasks.c.id == task_id)
            .values(status="closed", updated_at=now)
        )
        decision_id = int(
            conn.execute(
                insert(review_decisions).values(
                    task_id=task_id,
                    selection_json=encoded,
                    applied_by=actor,
                    before_json="{}",
                    after_json=encoded,
                    active=True,
                    created_at=now,
                    reversed_at=None,
                )
            ).inserted_primary_key[0]
        )
        directive_key = sha256_parts(["review_decision", str(decision_id)])
        directive_id = int(
            conn.execute(
                insert(knowledge_directives).values(
                    directive_key=directive_key,
                    task_id=task_id,
                    subject_type=str(task.get("subject_type", "task")),
                    subject_key=str(task.get("subject_key", task_id)),
                    selection_json=encoded,
                    evidence_signature=evidence_signature,
                    actor=actor,
                    source_version=directive_version,
                    active=True,
                    created_at=now,
                    revoked_at=None,
                )
            ).inserted_primary_key[0]
        )

    audit(
        actor,
        "review_vote",
        "review_vote",
        str(task_id),
        {},
        {
            "task_id": task_id,
            "task_title": task_title,
            "task_type": task_type,
            "selection": selection,
            "is_admin": is_admin,
            "votes": 1,
            "required": 1,
            "applied": True,
            "decision_id": decision_id,
            "directive_id": directive_id,
        },
    )
    increment_human_decisions()
    bump_data_version()
    audit(
        actor,
        "apply_review",
        "review_task",
        str(task_id),
        {},
        {"selection": selection, "directive_id": directive_id},
    )
    return {
        "applied": True,
        "votes": 1,
        "required": 1,
        "decision_id": decision_id,
        "directive_id": directive_id,
    }


def undo_decision(decision_id: int, actor: str = "Администратор") -> None:
    initialize_database()
    task_id = 0
    with get_engine().begin() as conn:
        row = conn.execute(
            select(review_decisions).where(
                review_decisions.c.id == decision_id,
                review_decisions.c.active.is_(True),
            )
        ).first()
        if not row:
            raise ValueError("Активное решение не найдено.")
        decision = dict(row._mapping)
        task_id = int(decision["task_id"])
        now = utcnow()
        conn.execute(
            update(knowledge_directives)
            .where(
                knowledge_directives.c.task_id == task_id,
                knowledge_directives.c.active.is_(True),
            )
            .values(active=False, revoked_at=now)
        )
        conn.execute(
            update(review_tasks)
            .where(review_tasks.c.id == task_id)
            .values(status="open", updated_at=now)
        )
        conn.execute(delete(review_votes).where(review_votes.c.task_id == task_id))
        conn.execute(
            update(review_decisions)
            .where(review_decisions.c.id == decision_id)
            .values(active=False, reversed_at=now)
        )

    bump_data_version()
    event_id = publish_event(
        "knowledge_directive_revoked",
        "review_decision",
        str(decision_id),
        {"task_id": task_id, "actor": actor},
        deduplication_key=f"undo:{decision_id}",
    )
    from .agent import run_until_event

    result = run_until_event(event_id, max_events=500, worker_id="undo-inline")
    if result["target_status"] != "completed":
        raise RuntimeError("Агент не смог перестроить базу после отмены решения.")
    audit(actor, "undo_review", "review_decision", str(decision_id), decision, {})


def recent_votes(limit: int = 300) -> list[dict[str, Any]]:
    initialize_database()
    query = (
        select(
            review_votes.c.id,
            review_votes.c.reviewer,
            review_votes.c.task_id,
            review_votes.c.selection_json,
            review_votes.c.is_admin,
            review_votes.c.created_at,
            review_tasks.c.title,
            review_tasks.c.task_type,
            review_tasks.c.status.label("task_status"),
        )
        .select_from(review_votes.join(review_tasks, review_votes.c.task_id == review_tasks.c.id))
        .order_by(review_votes.c.id.desc())
        .limit(limit)
    )
    result: list[dict[str, Any]] = []
    with get_engine().connect() as conn:
        for row in conn.execute(query):
            item = dict(row._mapping)
            try:
                selection = json.loads(str(item.pop("selection_json")) or "{}")
            except json.JSONDecodeError:
                selection = {}
            selected_res = selection.get("selected_res", [])
            item["selected_res"] = ", ".join(str(value) for value in selected_res) or "Не выбран"
            changes = []
            for key, label in (
                ("district", "район"),
                ("locality", "населенный пункт"),
                ("settlement", "СНТ / поселок"),
                ("street", "улица"),
            ):
                value = str(selection.get(key, "")).strip()
                if value:
                    changes.append(f"{label}: {value}")
            item["address_changes"] = "; ".join(changes) or "Без изменения адреса"
            item["vote_progress"] = "Решение принято"
            item["result"] = "Передано агенту" if item["task_status"] == "closed" else "Ожидает"
            result.append(item)
    return result


def recent_decisions(limit: int = 100) -> list[dict[str, Any]]:
    with get_engine().connect() as conn:
        query = (
            select(
                review_decisions.c.id,
                review_decisions.c.task_id,
                review_decisions.c.applied_by,
                review_decisions.c.selection_json,
                review_decisions.c.active,
                review_decisions.c.created_at,
                review_tasks.c.title,
                review_tasks.c.task_type,
            )
            .select_from(
                review_decisions.join(review_tasks, review_decisions.c.task_id == review_tasks.c.id)
            )
            .order_by(review_decisions.c.id.desc())
            .limit(limit)
        )
        return [dict(row._mapping) for row in conn.execute(query)]
