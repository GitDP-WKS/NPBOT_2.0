from __future__ import annotations

from res_ai_v2.import_service import import_plan
from res_ai_v2.importer import ImportPlan, SheetPlan
from res_ai_v2.repositories import knowledge_rows, list_review_tasks, stats


def make_plan(file_hash: str, rows: list[dict]) -> ImportPlan:
    prepared = []
    for index, row in enumerate(rows, start=2):
        prepared.append(
            {
                "branch": row.get("branch", ""),
                "res": row["res"],
                "known_res": True,
                "locality": row.get("locality", ""),
                "district": row.get("district", ""),
                "settlement": row.get("settlement", ""),
                "street": row.get("street", ""),
                "text": row.get("text", ""),
                "record_number": "",
                "sheet_name": "Лист1",
                "row_number": index,
                "raw": row,
            }
        )
    return ImportPlan(
        file_hash=file_hash,
        file_name=f"{file_hash}.xlsx",
        source_kind="mixed",
        sheets=[SheetPlan("Лист1", 0, {}, {}, [], prepared, [])],
        detected_rows=len(prepared),
        warnings=[],
    )


def test_duplicate_file_does_not_change_database(temp_db) -> None:
    plan = make_plan(
        "a" * 64,
        [{"branch": "ошибочный филиал", "res": "Лаишевский район электрических сетей", "locality": "Усады", "district": "Лаишевский"}],
    )
    first = import_plan(plan)
    second = import_plan(plan)
    assert first["already_loaded"] is False
    assert second["already_loaded"] is True
    assert stats()["mappings"] == 1
    row = knowledge_rows()[0]
    assert row["branch_name"] == "Приволжские сети"
    assert row["status"] == "consistent"
    assert row["source_confidence"] == 99.9


def test_same_name_in_two_districts_is_not_exact_address_conflict(temp_db) -> None:
    plan = make_plan(
        "b" * 64,
        [
            {"res": "Лаишевский район электрических сетей", "locality": "Усады", "district": "Лаишевский"},
            {"res": "Пригородный район электрических сетей", "locality": "Усады", "district": "Приволжский"},
        ],
    )
    import_plan(plan)
    values = knowledge_rows()
    assert len(values) == 2
    assert {row["status"] for row in values} == {"consistent"}
    assert {row["source_confidence"] for row in values} == {50.0}
    assert stats()["conflicts"] == 0


def test_same_full_address_with_two_res_creates_conflict(temp_db) -> None:
    plan = make_plan(
        "c" * 64,
        [
            {"res": "Лаишевский район электрических сетей", "locality": "Усады", "district": "Лаишевский"},
            {"res": "Пригородный район электрических сетей", "locality": "Усады", "district": "Лаишевский"},
        ],
    )
    import_plan(plan)
    assert stats()["conflicts"] == 1
    tasks = list_review_tasks("Иванов")
    assert tasks[0]["task_type"] == "mapping_conflict"
