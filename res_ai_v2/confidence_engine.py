from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .address_domain import CanonicalAddress


@dataclass(frozen=True)
class ConfidenceResult:
    score: float
    level: str
    factors: list[dict[str, Any]]
    explanation: str

    def payload(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "level": self.level,
            "factors": self.factors,
            "explanation": self.explanation,
        }


def _mean(values: list[float], default: float) -> float:
    return sum(values) / len(values) if values else default


def _freshness(observed: list[datetime | None], now: datetime) -> float:
    dates = [value.astimezone(UTC) for value in observed if value is not None]
    if not dates:
        return 0.5
    age_days = max(0.0, (now - max(dates)).total_seconds() / 86400.0)
    if age_days <= 30:
        return 1.0
    if age_days <= 180:
        return 0.8
    if age_days <= 730:
        return 0.55
    return 0.3


def evaluate_confidence(
    address: CanonicalAddress,
    observations: list[dict[str, Any]],
    *,
    conflict_count: int = 0,
    operator_decision: str = "",
    geodata_match: bool = False,
    now: datetime | None = None,
) -> ConfidenceResult:
    current = now or datetime.now(UTC)
    source_quality = _mean(
        [float(item.get("source_quality", 0.6) or 0.6) for item in observations], 0.6
    )
    source_accuracy = _mean(
        [float(item.get("source_accuracy", 0.5) or 0.5) for item in observations], 0.5
    )
    independent = max(
        0,
        sum(int(item.get("independent_evidence_count", 1) or 0) for item in observations),
    )
    technical_duplicates = sum(
        int(item.get("technical_duplicate_count", 0) or 0) for item in observations
    )
    freshness = _freshness([item.get("observed_at") for item in observations], current)

    factors: list[dict[str, Any]] = []
    score = 10.0

    def add(name: str, value: float, maximum: float, reason: str) -> None:
        nonlocal score
        points = round(max(0.0, min(maximum, value)), 2)
        score += points
        factors.append(
            {"name": name, "points": points, "maximum": maximum, "reason": reason}
        )

    add(
        "качество источника",
        source_quality * 20.0,
        20.0,
        f"средняя оценка источников {source_quality:.2f}",
    )
    add(
        "историческая точность источника",
        source_accuracy * 12.0,
        12.0,
        f"историческая точность {source_accuracy:.2f}",
    )
    independent_factor = min(1.0, math.log1p(independent) / math.log(5.0))
    add(
        "независимые доказательства",
        independent_factor * 18.0,
        18.0,
        f"независимых событий: {independent}",
    )
    add(
        "полнота адреса",
        address.completeness * 18.0,
        18.0,
        f"полнота канонического адреса {address.completeness:.2f}",
    )
    add(
        "районный контекст",
        8.0 if address.has_region_context else 0.0,
        8.0,
        "район или городской округ указан"
        if address.has_region_context
        else "районный контекст отсутствует",
    )
    add(
        "координаты",
        5.0 if address.latitude is not None and address.longitude is not None else 0.0,
        5.0,
        "координаты указаны" if address.latitude is not None else "координаты отсутствуют",
    )
    add(
        "географическая проверка",
        5.0 if geodata_match else 0.0,
        5.0,
        "географические данные подтверждают выбор"
        if geodata_match
        else "географическое подтверждение не применялось",
    )
    add(
        "актуальность",
        freshness * 7.0,
        7.0,
        f"коэффициент актуальности {freshness:.2f}",
    )

    decision_key = operator_decision.strip().lower()
    if decision_key in {"confirmed", "selected_other", "conditional", "operator_confirmed"}:
        add("решение оператора", 15.0, 15.0, "есть действующая директива оператора")
    else:
        add("решение оператора", 0.0, 15.0, "действующая директива отсутствует")

    penalties = 0.0
    if conflict_count:
        penalties += min(35.0, 18.0 + 6.0 * max(0, conflict_count - 1))
        factors.append(
            {
                "name": "противоречия",
                "points": -penalties,
                "maximum": 0.0,
                "reason": f"реальных противоречий: {conflict_count}",
            }
        )
    if not address.has_region_context and address.address_type in {
        "населенный пункт",
        "садовое товарищество",
        "территория",
    }:
        penalties += 12.0
        factors.append(
            {
                "name": "неоднозначность без района",
                "points": -12.0,
                "maximum": 0.0,
                "reason": "адрес нельзя однозначно сопоставить без района или координат",
            }
        )
    score -= penalties
    score = round(max(0.0, min(100.0, score)), 1)

    if score >= 85:
        level = "высокое"
    elif score >= 65:
        level = "среднее"
    elif score >= 45:
        level = "ограниченное"
    else:
        level = "недостаточное"

    explanation = (
        f"Доверие {score:.1f}/100 ({level}). "
        f"Независимых доказательств: {independent}; технических копий: "
        f"{technical_duplicates}. Технические копии на оценку не влияют."
    )
    return ConfidenceResult(score, level, factors, explanation)
