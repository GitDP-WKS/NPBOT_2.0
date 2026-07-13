from __future__ import annotations

from res_ai_v2.import_service import import_plan
from res_ai_v2.search import load_search_index, predict
from tests.test_import_and_analysis import make_plan


def test_ambiguous_locality_requires_district(temp_db) -> None:
    plan = make_plan(
        "f" * 64,
        [
            {"res": "Лаишевский район электрических сетей", "locality": "Усады", "district": "Лаишевский"},
            {"res": "Пригородный район электрических сетей", "locality": "Усады", "district": "Приволжский"},
        ],
    )
    import_plan(plan)
    index = load_search_index()
    ambiguous = predict("Усады", index=index, enqueue=False)
    assert ambiguous.status == "ambiguous"
    assert len({row["res"] for row in ambiguous.candidates}) == 2

    exact = predict("Лаишевский район, Усады", index=index, enqueue=False)
    assert exact.status == "final"
    assert exact.candidates[0]["res"] == "Лаишевский район электрических сетей"
    assert exact.confidence == 99.9


def test_substring_does_not_replace_locality(temp_db) -> None:
    plan = make_plan(
        "1" * 64,
        [{"res": "Чистопольский район электрических сетей", "locality": "Чистополь", "street": "Чистопольская"}],
    )
    import_plan(plan)
    result = predict("Чистопольский район Усады", index=load_search_index(), enqueue=False)
    assert result.status == "not_found"
