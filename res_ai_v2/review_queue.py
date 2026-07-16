from __future__ import annotations

import json
from datetime import timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, exists, or_, select, update
from sqlalchemy.exc import IntegrityError

from .db import get_engine, initialize_database, utcnow
from .normalize import normalize_text
from .pit_schema import review_task_leases
from .schema import review_tasks, review_votes

DEFAULT_LEASE_MINUTES = 15


def _task_dict(row, token: str, expires_at) -> dict[str, Any]:
    item = dict(row._mapping)
    item["payload"] = json.loads(str(item.pop("payload_json")) or "{}")
    item["lease_token"] = token
    item["lease_expires_at"] = expires_at
    return item


def _cleanup(conn, now) -> None:
    closed_tasks = select(review_tasks.c.id).where(review_tasks.c.status != "open")
    conn.execute(
        delete(review_task_leases).where(
            or_(
                review_task_leases.c.expires_at <= now,
                review_task_leases.c.task_id.in_(closed_tasks),
            )
        )
    )


def claim_review_task(
    reviewer: str,
    *,
    lease_minutes: int = DEFAULT_LEASE_MINUTES,
    exclude_ids: set[int] | None = None,
) -> dict[str, Any] | None:
    """Выдает уникальное задание, которое не видит другой проверяющий."""
    reviewer_key = normalize_text(reviewer)
    if not reviewer_key:
        raise ValueError("Укажите имя проверяющего.")
    initialize_database()
    engine = get_engine()
    now = utcnow()
    expires_at = now + timedelta(minutes=max(1, lease_minutes))
    excluded = sorted({int(value) for value in (exclude_ids or set()) if int(value) > 0})

    with engine.begin() as conn:
        _cleanup(conn, now)
        voted_tasks = select(review_votes.c.task_id).where(review_votes.c.reviewer == reviewer_key)
        conn.execute(
            delete(review_task_leases).where(
                review_task_leases.c.reviewer == reviewer_key,
                review_task_leases.c.task_id.in_(voted_tasks),
            )
        )

        existing_query = (
            select(review_tasks, review_task_leases.c.lease_token)
            .select_from(
                review_task_leases.join(
                    review_tasks,
                    review_task_leases.c.task_id == review_tasks.c.id,
                )
            )
            .where(
                review_task_leases.c.reviewer == reviewer_key,
                review_task_leases.c.expires_at > now,
                review_tasks.c.status == "open",
                review_tasks.c.id.not_in(voted_tasks),
            )
            .order_by(review_task_leases.c.claimed_at)
            .limit(1)
        )
        if excluded:
            existing_query = existing_query.where(review_tasks.c.id.not_in(excluded))
        existing = conn.execute(existing_query).first()
        if existing:
            conn.execute(
                update(review_task_leases)
                .where(review_task_leases.c.task_id == int(existing.id))
                .values(expires_at=expires_at, updated_at=now)
            )
            return _task_dict(existing, str(existing.lease_token), expires_at)

        leased_tasks = review_tasks.alias("leased_tasks")
        leased_rows = review_task_leases.alias("leased_rows")
        same_subject_leased = exists(
            select(leased_rows.c.task_id)
            .select_from(
                leased_rows.join(
                    leased_tasks,
                    leased_rows.c.task_id == leased_tasks.c.id,
                )
            )
            .where(
                leased_rows.c.expires_at > now,
                leased_tasks.c.subject_type == review_tasks.c.subject_type,
                leased_tasks.c.subject_key == review_tasks.c.subject_key,
            )
        )

        for _ in range(5):
            query = (
                select(review_tasks)
                .where(
                    review_tasks.c.status == "open",
                    review_tasks.c.id.not_in(voted_tasks),
                    ~same_subject_leased,
                )
                .order_by(
                    review_tasks.c.priority.desc(),
                    review_tasks.c.created_at,
                    review_tasks.c.id,
                )
                .limit(1)
            )
            if excluded:
                query = query.where(review_tasks.c.id.not_in(excluded))
            if engine.dialect.name == "postgresql":
                query = query.with_for_update(skip_locked=True)
            row = conn.execute(query).first()
            if not row:
                return None
            task_id = int(row.id)
            token = uuid4().hex
            try:
                with conn.begin_nested():
                    conn.execute(
                        review_task_leases.insert().values(
                            task_id=task_id,
                            reviewer=reviewer_key,
                            lease_token=token,
                            claimed_at=now,
                            expires_at=expires_at,
                            updated_at=now,
                        )
                    )
            except IntegrityError:
                continue
            return _task_dict(row, token, expires_at)
    return None


def validate_review_lease(task_id: int, reviewer: str, lease_token: str) -> None:
    reviewer_key = normalize_text(reviewer)
    now = utcnow()
    with get_engine().connect() as conn:
        row = conn.execute(
            select(review_task_leases.c.task_id).where(
                review_task_leases.c.task_id == task_id,
                review_task_leases.c.reviewer == reviewer_key,
                review_task_leases.c.lease_token == lease_token,
                review_task_leases.c.expires_at > now,
            )
        ).first()
    if not row:
        raise ValueError("Срок задания истек или оно уже передано другому проверяющему.")


def release_review_task(task_id: int, reviewer: str, lease_token: str) -> bool:
    reviewer_key = normalize_text(reviewer)
    with get_engine().begin() as conn:
        result = conn.execute(
            delete(review_task_leases).where(
                review_task_leases.c.task_id == task_id,
                review_task_leases.c.reviewer == reviewer_key,
                review_task_leases.c.lease_token == lease_token,
            )
        )
    return bool(result.rowcount)


def release_all_for_reviewer(reviewer: str) -> int:
    reviewer_key = normalize_text(reviewer)
    if not reviewer_key:
        return 0
    with get_engine().begin() as conn:
        result = conn.execute(
            delete(review_task_leases).where(review_task_leases.c.reviewer == reviewer_key)
        )
    return int(result.rowcount or 0)


def active_leases() -> int:
    now = utcnow()
    with get_engine().connect() as conn:
        return len(
            list(
                conn.scalars(
                    select(review_task_leases.c.task_id).where(
                        review_task_leases.c.expires_at > now
                    )
                )
            )
        )
