from __future__ import annotations

from sqlalchemy import func, select

from res_ai_v2.address_domain import canonicalize_address
from res_ai_v2.confidence_engine import evaluate_confidence
from res_ai_v2.db import get_engine
from res_ai_v2.domain_schema import canonical_observations, source_evidence
from res_ai_v2.explanations import active_conditional_rules
from res_ai_v2.import_service import import_plan
from res_ai_v2.pit_schema import pit_observations, pit_occurrences
from res_ai_v2.repositories import knowledge_rows, list_review_tasks, stats
from res_ai_v2.review_queue import claim_review_task
from res_ai_v2.review_service import submit_review_and_update_agent
from res_ai_v2.schema import source_files
from tests.test_import_and_analysis import make_plan

LAISHEVO = "Лаишевский район электрических сетей"
PRIGOROD = "Пригородный район электрических сетей"


def test_homonymous_localities_have_different_canonical_ids() -> None:
    first = canonicalize_address(
        {"locality": "с. Усады", "district": "Лаишевский", "street": "Центральная"}
    )
    second = canonicalize_address(
        {"locality": "с. Усады", "district": "Высокогорский", "street": "Центральная"}
    )
    assert first.ambiguity_key == second.ambiguity_key
    assert first.canonical_key != second.canonical_key
    assert first.context_key != second.context_key


def test_homonymous_snt_are_separate_by_district_and_coordinates() -> None:
    first = canonicalize_address(
        {
            "settlement": "СНТ Ромашка",
            "district": "Лаишевский",
            "latitude": "55.5001",
            "longitude": "49.2001",
        }
    )
    second = canonicalize_address(
        {
            "settlement": "СНТ Ромашка",
            "district": "Высокогорский",
            "latitude": "55.9001",
            "longitude": "49.5001",
        }
    )
    assert first.territory_type == "СНТ"
    assert first.ambiguity_key == second.ambiguity_key
    assert first.canonical_key != second.canonical_key


def test_same_name_in_different_districts_is_not_conflict(temp_db) -> None:
    import_plan(
        make_plan(
            "11" * 32,
            [
                {
                    "res": LAISHEVO,
                    "locality": "Усады",
                    "district": "Лаишевский",
                    "source_event_id": "event-1",
                },
                {
                    "res": PRIGOROD,
                    "locality": "Усады",
                    "district": "Приволжский",
                    "source_event_id": "event-2",
                },
            ],
        )
    )
    values = knowledge_rows()
    assert len(values) == 2
    assert stats()["conflicts"] == 0
    assert {item["address_key"] for item in values}.__len__() == 2


def test_same_snt_in_different_districts_is_not_conflict(temp_db) -> None:
    import_plan(
        make_plan(
            "12" * 32,
            [
                {
                    "res": LAISHEVO,
                    "settlement": "СНТ Ромашка",
                    "district": "Лаишевский",
                    "latitude": "55.5001",
                    "longitude": "49.2001",
                    "source_event_id": "snt-1",
                },
                {
                    "res": PRIGOROD,
                    "settlement": "СНТ Ромашка",
                    "district": "Высокогорский",
                    "latitude": "55.9001",
                    "longitude": "49.5001",
                    "source_event_id": "snt-2",
                },
            ],
        )
    )
    assert stats()["conflicts"] == 0
    with get_engine().connect() as conn:
        assert int(conn.scalar(select(func.count()).select_from(canonical_observations)) or 0) == 2


def test_missing_district_creates_task_and_does_not_guess(temp_db) -> None:
    import_plan(
        make_plan(
            "13" * 32,
            [
                {
                    "res": LAISHEVO,
                    "locality": "Александровка",
                    "district": "Лаишевский",
                    "source_event_id": "a-1",
                },
                {
                    "res": PRIGOROD,
                    "locality": "Александровка",
                    "district": "Высокогорский",
                    "source_event_id": "a-2",
                },
            ],
        )
    )
    import_plan(
        make_plan(
            "14" * 32,
            [
                {
                    "res": LAISHEVO,
                    "locality": "Александровка",
                    "source_event_id": "a-unknown",
                }
            ],
        )
    )
    tasks = list_review_tasks("Проверяющий")
    missing = [item for item in tasks if item["task_type"] == "missing_context"]
    assert missing
    assert missing[0]["payload"]["do_not_guess"] is True
    unqualified = [item for item in knowledge_rows() if not item["district"]]
    assert unqualified
    assert {item["status"] for item in unqualified} == {"ambiguous"}


def test_same_canonical_address_with_different_res_is_real_conflict(temp_db) -> None:
    import_plan(
        make_plan(
            "15" * 32,
            [
                {
                    "res": LAISHEVO,
                    "locality": "Столбище",
                    "district": "Лаишевский",
                    "street": "Центральная",
                    "source_event_id": "conflict-1",
                },
                {
                    "res": PRIGOROD,
                    "locality": "Столбище",
                    "district": "Лаишевский",
                    "street": "Центральная",
                    "source_event_id": "conflict-2",
                },
            ],
        )
    )
    assert stats()["conflicts"] == 1
    assert list_review_tasks("Проверяющий")[0]["task_type"] == "mapping_conflict"


def test_file_copy_is_technical_duplicate_without_recalculation(temp_db) -> None:
    row = {
        "res": LAISHEVO,
        "locality": "Усады",
        "district": "Лаишевский",
        "text": "Нет электричества",
        "source_system": "112",
        "source_event_id": "112-100",
    }
    first = import_plan(make_plan("16" * 32, [row]), wait_for_agent=False)
    second = import_plan(make_plan("17" * 32, [row]), wait_for_agent=False)
    assert first["imported"] == 1
    assert second["imported"] == 0
    assert second["technical_duplicates"] == 1
    assert second["event_id"] is None
    with get_engine().connect() as conn:
        assert int(conn.scalar(select(func.count()).select_from(source_files)) or 0) == 2
        assert int(conn.scalar(select(func.count()).select_from(pit_occurrences)) or 0) == 1
        assert int(
            conn.scalar(
                select(func.count()).select_from(source_evidence).where(
                    source_evidence.c.technical_duplicate.is_(True)
                )
            )
            or 0
        ) == 1


def test_independent_events_raise_evidence_count_but_copies_do_not(temp_db) -> None:
    base = {
        "res": LAISHEVO,
        "locality": "Усады",
        "district": "Лаишевский",
        "text": "Нет электричества",
        "source_system": "112",
    }
    import_plan(make_plan("18" * 32, [{**base, "source_event_id": "event-1"}]), wait_for_agent=False)
    import_plan(make_plan("19" * 32, [{**base, "source_event_id": "event-2"}]), wait_for_agent=False)
    for index in range(20, 30):
        import_plan(
            make_plan(f"{index:064x}", [{**base, "source_event_id": "event-2"}]),
            wait_for_agent=False,
        )
    with get_engine().connect() as conn:
        observation = conn.execute(select(pit_observations)).one()
    assert int(observation.occurrence_count) == 2
    assert int(observation.source_count) == 2


def test_conditional_operator_decision_keeps_both_correct_alternatives(temp_db) -> None:
    import_plan(
        make_plan(
            "20" * 32,
            [
                {
                    "res": LAISHEVO,
                    "locality": "Тестовый",
                    "district": "Лаишевский",
                    "source_event_id": "r-1",
                },
                {
                    "res": PRIGOROD,
                    "locality": "Тестовый",
                    "district": "Лаишевский",
                    "source_event_id": "r-2",
                },
            ],
        )
    )
    task = claim_review_task("Иванов")
    assert task is not None
    ambiguity_key = str(task["payload"]["address"]["ambiguity_key"])
    result = submit_review_and_update_agent(
        task["id"],
        "Иванов",
        {
            "decision_type": "both_by_condition",
            "selected_res": [LAISHEVO, PRIGOROD],
            "conditions": [
                {
                    "res": LAISHEVO,
                    "ambiguity_key": ambiguity_key,
                    "condition": {"street": "Центральная"},
                },
                {
                    "res": PRIGOROD,
                    "ambiguity_key": ambiguity_key,
                    "condition": {"street": "Новая"},
                },
            ],
        },
        False,
        task["lease_token"],
    )
    assert result["applied"] is True
    assert result["recalculation_scope"] == "full"
    assert len(active_conditional_rules(ambiguity_key)) >= 2
    assert {item["status"] for item in knowledge_rows()} == {"conditional"}


def test_confidence_is_explainable_and_ignores_technical_copy_count() -> None:
    address = canonicalize_address(
        {"locality": "Усады", "district": "Лаишевский", "street": "Центральная"}
    )
    base = {
        "source_quality": 0.8,
        "source_accuracy": 0.9,
        "independent_evidence_count": 2,
        "technical_duplicate_count": 0,
    }
    first = evaluate_confidence(address, [base])
    second = evaluate_confidence(address, [{**base, "technical_duplicate_count": 1000}])
    assert first.score == second.score
    assert first.factors
    assert "Технические копии" in second.explanation
