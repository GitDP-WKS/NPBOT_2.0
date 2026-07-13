from __future__ import annotations


def uniqueness_confidence(distinct_contexts: int) -> float:
    """Доверие имени обратно пропорционально числу разных адресных контекстов."""
    distinct_contexts = max(1, int(distinct_contexts))
    return round(99.9 / distinct_contexts, 1)


def decision_confidence(
    *,
    mapping_confidence: float,
    matched_district: bool,
    matched_street: bool,
    matched_settlement: bool,
    ambiguous: bool,
    human_verified: bool,
) -> float:
    if human_verified and not ambiguous:
        return 100.0
    value = float(mapping_confidence)
    if matched_district:
        value = max(value, 99.9)
    if matched_settlement:
        value = min(99.9, value + 2.0)
    if matched_street:
        value = min(99.9, value + 2.0)
    if ambiguous:
        value = min(value, 50.0)
    return round(min(99.9, value), 1)
