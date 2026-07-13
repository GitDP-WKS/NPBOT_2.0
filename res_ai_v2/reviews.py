from __future__ import annotations

import json
from typing import Any

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.exc import IntegrityError

from .config import load_settings
from .db import bump_data_version, get_engine, initialize_database, utcnow
from .normalize import normalize_text, stable_json
from .repositories import audit, increment_human_decisions
from .review_helpers import _apply
from .schema import address_mappings, query_rules, review_decisions, review_tasks, review_votes, text_examples


def submit_review(task_id: int, reviewer: str, selection: dict[str, Any], is_admin: bool) -> dict[str, Any]:
    actor, reviewer_key = " ".join(reviewer.strip().split()), normalize_text(reviewer)
    if not reviewer_key: raise ValueError("Укажите имя проверяющего.")
    initialize_database(); encoded = stable_json(selection); required = load_settings().review_votes_required
    with get_engine().begin() as conn:
        row = conn.execute(select(review_tasks).where(review_tasks.c.id == task_id, review_tasks.c.status == "open")).first()
        if not row: return {"applied": False, "votes": 0, "message": "Задание уже закрыто."}
        task = dict(row._mapping)
        try: conn.execute(insert(review_votes).values(task_id=task_id, reviewer=reviewer_key, selection_json=encoded, is_admin=is_admin, created_at=utcnow()))
        except IntegrityError as exc: raise ValueError("Вы уже проверяли это задание.") from exc
        votes = 1 if is_admin else int(conn.scalar(select(func.count()).select_from(review_votes).where(review_votes.c.task_id == task_id, review_votes.c.selection_json == encoded, review_votes.c.is_admin.is_(False))) or 0)
        if not (is_admin or votes >= required): return {"applied": False, "votes": votes, "required": required}
        before, after = _apply(conn, task, selection, actor, votes)
        conn.execute(update(review_tasks).where(review_tasks.c.id == task_id).values(status="closed", updated_at=utcnow()))
        conn.execute(insert(review_decisions).values(task_id=task_id, selection_json=encoded, applied_by=actor, before_json=stable_json(before), after_json=stable_json(after), active=True, created_at=utcnow(), reversed_at=None))
    increment_human_decisions(); bump_data_version(); audit(actor, "apply_review", "review_task", str(task_id), before, after)
    return {"applied": True, "votes": votes, "required": required}


def undo_decision(decision_id: int, actor: str = "Администратор") -> None:
    with get_engine().begin() as conn:
        row = conn.execute(select(review_decisions).where(review_decisions.c.id == decision_id, review_decisions.c.active.is_(True))).first()
        if not row: raise ValueError("Активное решение не найдено.")
        decision = dict(row._mapping); before = json.loads(str(decision["before_json"]) or "{}"); task_id = int(decision["task_id"])
        for mapping in before.get("mappings", []):
            values = {key: mapping[key] for key in ("status", "source_confidence", "human_confirmations", "active", "branch_name", "res_name") if key in mapping}; values["updated_at"] = utcnow(); conn.execute(update(address_mappings).where(address_mappings.c.id == int(mapping["id"])).values(**values))
        if before.get("created_mapping_ids"): conn.execute(delete(address_mappings).where(address_mappings.c.id.in_([int(x) for x in before["created_mapping_ids"]])))
        if before.get("created_text_ids"): conn.execute(delete(text_examples).where(text_examples.c.id.in_([int(x) for x in before["created_text_ids"]])))
        for example in before.get("previous_texts", []): conn.execute(update(text_examples).where(text_examples.c.id == int(example["id"])).values(**{key: example[key] for key in ("raw_text", "normalized_text", "address_id", "res_name", "branch_name", "status", "human_confirmations", "weight", "updated_at") if key in example}))
        task = conn.execute(select(review_tasks).where(review_tasks.c.id == task_id)).first(); payload = json.loads(str(task.payload_json) or "{}") if task else {}; normalized = normalize_text(str(payload.get("query_text", "")))
        if normalized:
            old = before.get("query_rule")
            if old: conn.execute(update(query_rules).where(query_rules.c.normalized_query == normalized).values(raw_query=old["raw_query"], selection_json=old["selection_json"], created_by=old["created_by"], updated_at=utcnow()))
            else: conn.execute(delete(query_rules).where(query_rules.c.normalized_query == normalized))
        conn.execute(update(review_tasks).where(review_tasks.c.id == task_id).values(status="open", updated_at=utcnow())); conn.execute(delete(review_votes).where(review_votes.c.task_id == task_id)); conn.execute(update(review_decisions).where(review_decisions.c.id == decision_id).values(active=False, reversed_at=utcnow()))
    bump_data_version(); audit(actor, "undo_review", "review_decision", str(decision_id), decision, {})


def recent_decisions(limit: int = 100) -> list[dict[str, Any]]:
    with get_engine().connect() as conn:
        query = select(review_decisions.c.id, review_decisions.c.task_id, review_decisions.c.applied_by, review_decisions.c.selection_json, review_decisions.c.active, review_decisions.c.created_at, review_tasks.c.title, review_tasks.c.task_type).select_from(review_decisions.join(review_tasks, review_decisions.c.task_id == review_tasks.c.id)).order_by(review_decisions.c.id.desc()).limit(limit)
        return [dict(row._mapping) for row in conn.execute(query)]
