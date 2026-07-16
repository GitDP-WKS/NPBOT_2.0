from __future__ import annotations


def _reset_db(db) -> None:
    db.get_engine.cache_clear()
    db.initialize_database.cache_clear()


def test_claim_skips_tasks_already_reviewed_by_same_reviewer(tmp_path, monkeypatch):
    from sqlalchemy import insert

    from res_ai_v2 import db
    from res_ai_v2.review_queue import claim_review_task
    from res_ai_v2.schema import review_tasks, review_votes

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'review.db'}")
    _reset_db(db)
    db.initialize_database()

    now = db.utcnow()
    with db.get_engine().begin() as conn:
        first_id = int(
            conn.execute(
                insert(review_tasks).values(
                    task_key="test:borisoglebsk",
                    task_type="address_conflict",
                    subject_type="address",
                    subject_key="borisoglebsk",
                    title="Борисоглебск",
                    payload_json='{"address":{"locality":"Борисоглебск"},"options":[{"res":"Советский РЭС"}]}',
                    priority=100,
                    status="open",
                    created_at=now,
                    updated_at=now,
                )
            ).inserted_primary_key[0]
        )
        second_id = int(
            conn.execute(
                insert(review_tasks).values(
                    task_key="test:usady",
                    task_type="address_conflict",
                    subject_type="address",
                    subject_key="usady",
                    title="Усады",
                    payload_json='{"address":{"locality":"Усады"},"options":[{"res":"Лаишевский РЭС"}]}',
                    priority=50,
                    status="open",
                    created_at=now,
                    updated_at=now,
                )
            ).inserted_primary_key[0]
        )
        conn.execute(
            insert(review_votes).values(
                task_id=first_id,
                reviewer="администратор",
                selection_json='{"decision_type":"confirmed","selected_res":["Советский РЭС"]}',
                is_admin=True,
                created_at=now,
            )
        )

    task = claim_review_task("Администратор")
    assert task is not None
    assert int(task["id"]) == second_id


def test_same_subject_is_not_leased_to_two_reviewers(tmp_path, monkeypatch):
    from sqlalchemy import insert

    from res_ai_v2 import db
    from res_ai_v2.review_queue import claim_review_task
    from res_ai_v2.schema import review_tasks

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'leases.db'}")
    _reset_db(db)
    db.initialize_database()

    now = db.utcnow()
    with db.get_engine().begin() as conn:
        for index in range(2):
            conn.execute(
                insert(review_tasks).values(
                    task_key=f"test:same:{index}",
                    task_type="address_conflict",
                    subject_type="address",
                    subject_key="same-address",
                    title=f"Одинаковое задание {index}",
                    payload_json='{"address":{"locality":"Усады"},"options":[{"res":"Лаишевский РЭС"}]}',
                    priority=100 - index,
                    status="open",
                    created_at=now,
                    updated_at=now,
                )
            )
        conn.execute(
            insert(review_tasks).values(
                task_key="test:other",
                task_type="address_conflict",
                subject_type="address",
                subject_key="other-address",
                title="Другое задание",
                payload_json='{"address":{"locality":"Казань"},"options":[{"res":"Советский РЭС"}]}',
                priority=10,
                status="open",
                created_at=now,
                updated_at=now,
            )
        )

    first = claim_review_task("Оператор 1")
    second = claim_review_task("Оператор 2")
    assert first is not None and second is not None
    assert first["subject_key"] != second["subject_key"]
