from __future__ import annotations

from sqlalchemy import insert, select

from res_ai_v2.db import get_engine, utcnow
from res_ai_v2.pit_schema import review_task_leases
from res_ai_v2.review_queue import claim_review_task
from res_ai_v2.schema import review_tasks, review_votes


def _add_task(key: str, priority: int = 10) -> int:
    now = utcnow()
    with get_engine().begin() as conn:
        return int(
            conn.execute(
                insert(review_tasks).values(
                    task_key=key,
                    task_type="prediction_review",
                    subject_type="query",
                    subject_key=key,
                    title=key,
                    payload_json='{"address":{"locality":"Казань"},"options":[]}',
                    priority=priority,
                    status="open",
                    created_at=now,
                    updated_at=now,
                )
            ).inserted_primary_key[0]
        )


def test_different_sessions_receive_different_tasks(temp_db):
    first_id = _add_task("task-1", 20)
    second_id = _add_task("task-2", 10)

    first = claim_review_task("Иванов", lease_owner="Иванов::session-a")
    second = claim_review_task("Петров", lease_owner="Петров::session-b")

    assert first is not None
    assert second is not None
    assert int(first["id"]) == first_id
    assert int(second["id"]) == second_id
    assert int(first["id"]) != int(second["id"])


def test_reviewed_open_task_is_skipped_and_stale_lease_is_removed(temp_db):
    reviewed_id = _add_task("reviewed", 20)
    next_id = _add_task("next", 10)
    owner = "admin::session-a"
    first = claim_review_task("Администратор", lease_owner=owner)
    assert first is not None
    assert int(first["id"]) == reviewed_id

    with get_engine().begin() as conn:
        conn.execute(
            insert(review_votes).values(
                task_id=reviewed_id,
                reviewer="администратор",
                selection_json="{}",
                is_admin=True,
                created_at=utcnow(),
            )
        )

    second = claim_review_task("Администратор", lease_owner=owner)

    assert second is not None
    assert int(second["id"]) == next_id
    with get_engine().connect() as conn:
        stale = conn.scalar(
            select(review_task_leases.c.task_id).where(
                review_task_leases.c.task_id == reviewed_id
            )
        )
    assert stale is None
