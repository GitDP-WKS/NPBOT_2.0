from __future__ import annotations


def _reset(db) -> None:
    db.get_engine.cache_clear()
    db.initialize_database.cache_clear()


def _insert_task(conn, review_tasks, now, *, task_key: str, subject_key: str, priority: int):
    from sqlalchemy import insert

    return int(
        conn.execute(
            insert(review_tasks).values(
                task_key=task_key,
                task_type="address_conflict",
                subject_type="address",
                subject_key=subject_key,
                title=subject_key,
                payload_json='{"address":{"locality":"Казань"},"options":[{"res":"Советский РЭС"}]}',
                priority=priority,
                status="open",
                created_at=now,
                updated_at=now,
            )
        ).inserted_primary_key[0]
    )


def test_reviewed_task_is_not_returned_to_same_person(tmp_path, monkeypatch):
    from sqlalchemy import insert

    from res_ai_v2 import db
    from res_ai_v2.review_queue import claim_review_task
    from res_ai_v2.schema import review_tasks, review_votes

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'reviewed.db'}")
    _reset(db)
    db.initialize_database()
    now = db.utcnow()

    with db.get_engine().begin() as conn:
        first = _insert_task(
            conn,
            review_tasks,
            now,
            task_key="test:borisoglebsk",
            subject_key="borisoglebsk",
            priority=100,
        )
        second = _insert_task(
            conn,
            review_tasks,
            now,
            task_key="test:next",
            subject_key="next",
            priority=10,
        )
        conn.execute(
            insert(review_votes).values(
                task_id=first,
                reviewer="администратор",
                selection_json='{"decision_type":"confirmed","selected_res":["Советский РЭС"]}',
                is_admin=True,
                created_at=now,
            )
        )

    task = claim_review_task(
        "Администратор",
        lease_owner="Администратор::session-a",
    )
    assert task is not None
    assert int(task["id"]) == second


def test_same_subject_is_not_given_to_two_sessions(tmp_path, monkeypatch):
    from res_ai_v2 import db
    from res_ai_v2.review_queue import claim_review_task
    from res_ai_v2.schema import review_tasks

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'subjects.db'}")
    _reset(db)
    db.initialize_database()
    now = db.utcnow()

    with db.get_engine().begin() as conn:
        _insert_task(
            conn,
            review_tasks,
            now,
            task_key="test:same:1",
            subject_key="same-address",
            priority=100,
        )
        _insert_task(
            conn,
            review_tasks,
            now,
            task_key="test:same:2",
            subject_key="same-address",
            priority=90,
        )
        _insert_task(
            conn,
            review_tasks,
            now,
            task_key="test:other",
            subject_key="other-address",
            priority=10,
        )

    first = claim_review_task("Оператор 1", lease_owner="Оператор 1::a")
    second = claim_review_task("Оператор 2", lease_owner="Оператор 2::b")
    assert first is not None and second is not None
    assert first["subject_key"] != second["subject_key"]


def test_skipped_task_is_not_immediately_returned(tmp_path, monkeypatch):
    from res_ai_v2 import db
    from res_ai_v2.review_queue import claim_review_task
    from res_ai_v2.schema import review_tasks

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'skipped.db'}")
    _reset(db)
    db.initialize_database()
    now = db.utcnow()

    with db.get_engine().begin() as conn:
        first_id = _insert_task(
            conn,
            review_tasks,
            now,
            task_key="test:first",
            subject_key="first",
            priority=100,
        )
        second_id = _insert_task(
            conn,
            review_tasks,
            now,
            task_key="test:second",
            subject_key="second",
            priority=10,
        )

    owner = "Оператор::session"
    first = claim_review_task("Оператор", lease_owner=owner)
    assert first is not None and int(first["id"]) == first_id

    second = claim_review_task(
        "Оператор",
        lease_owner=owner,
        exclude_ids={first_id},
    )
    assert second is not None
    assert int(second["id"]) == second_id
