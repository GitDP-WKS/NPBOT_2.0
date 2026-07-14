from __future__ import annotations

import pytest

from res_ai_v2.review_policy import (
    decision_label,
    is_fundamental_decision,
    normalize_review_selection,
)

FIRST = "Лаишевский район электрических сетей"
SECOND = "Пригородный район электрических сетей"


@pytest.mark.parametrize(
    ("decision_type", "selected", "fundamental"),
    [
        ("confirmed", [FIRST], False),
        ("selected_other", [SECOND], False),
        ("insufficient_data", [], False),
        ("source_error", [], False),
        ("skip", [], False),
    ],
)
def test_simple_decision_types_are_normalized(
    decision_type: str,
    selected: list[str],
    fundamental: bool,
) -> None:
    result = normalize_review_selection(
        {"decision_type": decision_type, "selected_res": selected}
    )
    assert result["decision_type"] == decision_type
    assert result["selected_res"] == selected
    assert is_fundamental_decision(result) is fundamental
    assert decision_label(result)


@pytest.mark.parametrize("decision_type", ["both_by_district", "both_by_condition", "conditional"])
def test_conditional_decisions_require_explainable_conditions(decision_type: str) -> None:
    result = normalize_review_selection(
        {
            "decision_type": decision_type,
            "selected_res": [FIRST, SECOND],
            "conditions": [
                {"res": FIRST, "condition": {"district": "Лаишевский"}},
                {"res": SECOND, "condition": {"district": "Высокогорский"}},
            ],
        }
    )
    assert is_fundamental_decision(result) is True
    assert len(result["conditions"]) == 2


def test_conditional_decision_without_conditions_is_rejected() -> None:
    with pytest.raises(ValueError, match="признаки"):
        normalize_review_selection(
            {
                "decision_type": "both_by_district",
                "selected_res": [FIRST, SECOND],
                "conditions": [],
            }
        )


def test_unknown_decision_is_rejected() -> None:
    with pytest.raises(ValueError, match="Неизвестный"):
        normalize_review_selection(
            {"decision_type": "guess", "selected_res": [FIRST]}
        )
