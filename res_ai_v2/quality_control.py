from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Iterable

from .structure import CURRENT_STRUCTURE


def _segment_metrics(records: list[dict[str, Any]], field: str) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[str(record.get(field) or "unknown")].append(record)
    result: dict[str, dict[str, Any]] = {}
    for key, items in grouped.items():
        correct = sum(str(item["expected"]) == str(item["predicted"]) for item in items)
        result[key] = {
            "accuracy": correct / len(items),
            "support": len(items),
            "errors": len(items) - correct,
        }
    return result


def build_quality_metrics(records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = [dict(record) for record in records]
    if not rows:
        return {
            "accuracy": 0.0,
            "rows": 0,
            "per_res": {},
            "per_branch": {},
            "per_address_type": {},
            "per_source": {},
            "confusion": [],
        }
    for row in rows:
        row.setdefault("branch", CURRENT_STRUCTURE.get(str(row.get("expected", "")), ""))
        row.setdefault("address_type", "неопределенный")
        row.setdefault("source_system", "unknown")
    correct = sum(str(row["expected"]) == str(row["predicted"]) for row in rows)
    confusion = Counter(
        (str(row["expected"]), str(row["predicted"]))
        for row in rows
        if str(row["expected"]) != str(row["predicted"])
    )
    return {
        "accuracy": correct / len(rows),
        "rows": len(rows),
        "per_res": _segment_metrics(rows, "expected"),
        "per_branch": _segment_metrics(rows, "branch"),
        "per_address_type": _segment_metrics(rows, "address_type"),
        "per_source": _segment_metrics(rows, "source_system"),
        "confusion": [
            {"expected": expected, "predicted": predicted, "count": count}
            for (expected, predicted), count in confusion.most_common(100)
        ],
    }


def _regressions(
    current: dict[str, Any],
    previous: dict[str, Any],
    field: str,
    *,
    tolerance: float,
    minimum_support: int,
    label: str,
) -> list[str]:
    reasons: list[str] = []
    for key, values in current.get(field, {}).items():
        if int(values.get("support", 0)) < minimum_support:
            continue
        previous_values = previous.get(field, {}).get(key)
        if not previous_values:
            continue
        if float(values.get("accuracy", 0.0)) + tolerance < float(
            previous_values.get("accuracy", 0.0)
        ):
            reasons.append(f"Ухудшилось качество {label}: {key}")
    return reasons


def quality_gate_extended(
    current: dict[str, Any],
    previous: dict[str, Any] | None,
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if float(current.get("accuracy", 0.0)) < 0.80:
        reasons.append("Общая точность ниже 80%")
    if previous:
        if float(current.get("accuracy", 0.0)) + 0.002 < float(previous.get("accuracy", 0.0)):
            reasons.append("Общая точность ухудшилась")
        reasons.extend(
            _regressions(
                current,
                previous,
                "per_res",
                tolerance=0.03,
                minimum_support=5,
                label="по РЭС",
            )
        )
        reasons.extend(
            _regressions(
                current,
                previous,
                "per_branch",
                tolerance=0.02,
                minimum_support=10,
                label="по филиалу",
            )
        )
        reasons.extend(
            _regressions(
                current,
                previous,
                "per_address_type",
                tolerance=0.03,
                minimum_support=10,
                label="по типу адреса",
            )
        )
        reasons.extend(
            _regressions(
                current,
                previous,
                "per_source",
                tolerance=0.05,
                minimum_support=10,
                label="по источнику",
            )
        )
    return not reasons, list(dict.fromkeys(reasons))
