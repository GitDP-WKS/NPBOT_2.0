from __future__ import annotations

import io
import json
from pathlib import Path

import pandas as pd
from sqlalchemy import Column, Integer, MetaData, String, Table, Text, insert, select

from res_ai_v2.admin_service import request_full_analysis
from res_ai_v2.agent import run_until_event
from res_ai_v2.backup_service import backup_snapshot, request_restore
from res_ai_v2.daily_audit import ensure_daily_audit, latest_daily_audit
from res_ai_v2.db import get_engine, utcnow
from res_ai_v2.import_service import import_plan
from res_ai_v2.importer import inspect_excel
from res_ai_v2.legacy import migrate_legacy
from res_ai_v2.modeling import rollback_model
from res_ai_v2.normalize import stable_json
from res_ai_v2.repositories import knowledge_rows, list_review_tasks
from res_ai_v2.review_queue import claim_review_task, release_review_task
from res_ai_v2.review_service import submit_review_and_update_agent
from res_ai_v2.schema import model_versions
from tests.test_import_and_analysis import make_plan

FIRST = "Лаишевский район электрических сетей"
SECOND = "Пригородный район электрических сетей"


def _legacy_table() -> Table:
    metadata = MetaData()
    return Table(
        "res_ai_knowledge",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("branch", Text),
        Column("res", Text),
        Column("locality", Text),
        Column("district", Text),
        Column("settlement", Text),
        Column("street", Text),
        Column("text", Text),
        Column("status", String(32)),
        Column("confirmations", Integer),
    )


def _excel_bytes() -> bytes:
    stream = io.BytesIO()
    pd.DataFrame(
        [
            {
                "РЭС": FIRST,
                "Населенный пункт": "Куюки",
                "Район": "Пестречинский",
                "Улица": "Центральная",
                "Номер обращения": "excel-event-1",
                "Источник": "112",
            }
        ]
    ).to_excel(stream, index=False)
    return stream.getvalue()


def _model_row(version: str, status: str) -> dict:
    metrics = {
        "accuracy": 0.9,
        "macro_f1": 0.9,
        "per_res": {},
        "per_branch": {},
        "per_address_type": {},
        "per_source": {},
        "gate_reasons": [],
    }
    return {
        "version": version,
        "status": status,
        "algorithm": "e2e-test",
        "training_signature": version,
        "metrics_json": stable_json(metrics),
        "confusion_json": "[]",
        "model_blob": b"e2e",
        "gate_passed": True,
        "created_at": utcnow(),
        "published_at": utcnow() if status == "published" else None,
    }


def test_complete_end_to_end_scenario(temp_db, monkeypatch) -> None:
    monkeypatch.setenv("RES_AI_DISABLE_BACKGROUND_WORKER", "1")
    completed: list[int] = []
    engine = get_engine()

    legacy = _legacy_table()
    legacy.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(
            insert(legacy).values(
                id=1,
                branch="Приволжские сети",
                res=FIRST,
                locality="Старая деревня",
                district="Лаишевский",
                settlement="",
                street="Центральная",
                text="",
                status="approved",
                confirmations=1,
            )
        )
    migrated = migrate_legacy("Сквозной тест")
    assert migrated["rows"] == 1
    completed.append(1)

    content = _excel_bytes()
    plan = inspect_excel(content, "новый.xlsx")
    uploaded = import_plan(plan, actor="Сквозной тест")
    assert uploaded["imported"] == 1
    completed.append(2)

    repeated = import_plan(plan, actor="Сквозной тест")
    assert repeated["already_loaded"] is True
    completed.append(3)

    renamed = inspect_excel(content, "копия-под-другим-именем.xlsx")
    renamed_result = import_plan(renamed, actor="Сквозной тест")
    assert renamed_result["already_loaded"] is True
    completed.append(4)

    partial = import_plan(
        make_plan(
            "81" * 32,
            [
                {
                    "res": FIRST,
                    "locality": "Куюки",
                    "district": "Пестречинский",
                    "street": "Центральная",
                    "source_system": "112",
                    "source_event_id": "excel-event-1",
                },
                {
                    "res": FIRST,
                    "locality": "Богатые Сабы",
                    "district": "Сабинский",
                    "source_system": "112",
                    "source_event_id": "partial-new",
                },
            ],
        )
    )
    assert partial["technical_duplicates"] == 1
    assert partial["imported"] == 1
    completed.append(5)

    import_plan(
        make_plan(
            "82" * 32,
            [
                {
                    "res": FIRST,
                    "locality": "Спорный",
                    "district": "Лаишевский",
                    "street": "Новая",
                    "source_system": "112",
                    "source_event_id": "conflict-a",
                },
                {
                    "res": SECOND,
                    "locality": "Спорный",
                    "district": "Лаишевский",
                    "street": "Новая",
                    "source_system": "112",
                    "source_event_id": "conflict-b",
                },
            ],
        )
    )
    assert any(task["task_type"] == "mapping_conflict" for task in list_review_tasks("А"))
    completed.append(6)

    import_plan(
        make_plan(
            "83" * 32,
            [
                {
                    "res": FIRST,
                    "locality": "Александровка",
                    "district": "Лаишевский",
                    "source_event_id": "homonym-a",
                },
                {
                    "res": SECOND,
                    "locality": "Александровка",
                    "district": "Высокогорский",
                    "source_event_id": "homonym-b",
                },
            ],
        )
    )
    homonyms = [row for row in knowledge_rows() if row["locality"] == "Александровка"]
    assert len({row["address_key"] for row in homonyms}) == 2
    completed.append(7)

    import_plan(
        make_plan(
            "84" * 32,
            [
                {
                    "res": FIRST,
                    "settlement": "СНТ Ромашка",
                    "district": "Лаишевский",
                    "latitude": "55.5",
                    "longitude": "49.2",
                    "source_event_id": "snt-a",
                },
                {
                    "res": SECOND,
                    "settlement": "СНТ Ромашка",
                    "district": "Высокогорский",
                    "latitude": "55.9",
                    "longitude": "49.5",
                    "source_event_id": "snt-b",
                },
            ],
        )
    )
    snt_rows = [row for row in knowledge_rows() if row["settlement"] == "СНТ Ромашка"]
    assert len({row["address_key"] for row in snt_rows}) == 2
    completed.append(8)

    import_plan(
        make_plan(
            "85" * 32,
            [
                {
                    "res": FIRST,
                    "locality": "Александровка",
                    "source_event_id": "homonym-no-district",
                }
            ],
        )
    )
    assert any(task["task_type"] == "missing_context" for task in list_review_tasks("Б"))
    completed.append(9)

    qualified = [row for row in knowledge_rows() if row["locality"] == "Александровка" and row["district"]]
    assert qualified and all(row["status"] != "ambiguous" for row in qualified)
    completed.append(10)

    first_task = claim_review_task("Оператор 1")
    assert first_task is not None
    completed.append(11)

    second_task = claim_review_task("Оператор 2")
    assert second_task is not None
    assert second_task["id"] != first_task["id"]
    completed.append(12)
    release_review_task(second_task["id"], "Оператор 2", second_task["lease_token"])

    decision = submit_review_and_update_agent(
        first_task["id"],
        "Оператор 1",
        {"decision_type": "confirmed", "selected_res": [FIRST]},
        False,
        first_task["lease_token"],
    )
    assert decision["applied"] is True
    completed.append(13)
    assert decision["recalculation_scope"] == "local"
    completed.append(14)

    import_plan(
        make_plan(
            "86" * 32,
            [
                {
                    "res": SECOND,
                    "locality": "Спорный",
                    "district": "Лаишевский",
                    "street": "Новая",
                    "source_system": "112",
                    "source_event_id": "new-conflict-after-decision",
                }
            ],
        )
    )
    assert any(task["task_type"] == "directive_challenge" for task in list_review_tasks("Оператор 3"))
    completed.append(15)

    daily_event = ensure_daily_audit()
    assert daily_event is not None
    daily_result = run_until_event(daily_event, max_events=1000, worker_id="e2e-daily")
    assert daily_result["target_status"] == "completed"
    assert latest_daily_audit()["status"] == "completed"
    completed.append(16)

    manual = request_full_analysis("Сквозной тест", wait_for_agent=True)
    assert manual["status"] == "completed"
    completed.append(17)

    snapshot = backup_snapshot()
    assert json.loads(snapshot)["format"] == "res_ai_v3_full_backup"
    completed.append(18)

    import_plan(
        make_plan(
            "87" * 32,
            [
                {
                    "res": FIRST,
                    "locality": "После копии",
                    "district": "Лаишевский",
                    "source_event_id": "after-backup",
                }
            ],
        )
    )
    restored = request_restore(snapshot, "Сквозной тест", wait_for_agent=True)
    assert restored["status"] == "completed"
    assert not [row for row in knowledge_rows() if row["locality"] == "После копии"]
    completed.append(19)

    with engine.begin() as conn:
        conn.execute(insert(model_versions).values(**_model_row("e2e-old", "archived")))
        conn.execute(insert(model_versions).values(**_model_row("e2e-current", "published")))
    rollback_model("e2e-old", "Сквозной тест")
    with engine.connect() as conn:
        published = conn.scalar(
            select(model_versions.c.version).where(model_versions.c.status == "published")
        )
    assert published == "e2e-old"
    completed.append(20)

    import app as streamlit_entry

    assert callable(streamlit_entry.main)
    completed.append(21)

    from res_ai_v2.api import health as api_health

    assert api_health()["status"] == "ok"
    completed.append(22)

    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "Checkout exact commit" in workflow
    assert "Load test 50000" in workflow
    assert "Load test 200000" in workflow
    assert "Upload CI evidence" in workflow
    completed.append(23)

    assert completed == list(range(1, 24))
