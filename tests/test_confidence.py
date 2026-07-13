from res_ai_v2.confidence import decision_confidence, uniqueness_confidence


def test_uniqueness_confidence_is_proportional() -> None:
    assert uniqueness_confidence(1) == 99.9
    assert uniqueness_confidence(2) == 50.0
    assert uniqueness_confidence(3) == 33.3


def test_district_resolves_name_ambiguity() -> None:
    assert decision_confidence(
        mapping_confidence=50.0,
        matched_district=True,
        matched_street=False,
        matched_settlement=False,
        ambiguous=False,
        human_verified=False,
    ) == 99.9
