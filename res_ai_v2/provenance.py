from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .normalize import normalize_text, sha256_parts, stable_json

_SOURCE_SYSTEM_KEYS = (
    "source_system",
    "source",
    "источник",
    "система",
    "реестр",
    "канал",
)
_EVENT_KEYS = (
    "source_event_id",
    "event_id",
    "card_id",
    "номер карточки",
    "номер обращения",
    "идентификатор события",
    "record_number",
)
_DATE_KEYS = ("observed_at", "event_date", "дата", "дата события", "created_at")
_QUALITY_BY_KIND = {
    "legacy": 0.55,
    "labeled_texts": 0.65,
    "mixed": 0.72,
    "address_registry": 0.82,
    "operator": 0.98,
    "geodata": 0.96,
}


def _raw_map(row: dict[str, Any]) -> dict[str, Any]:
    raw = row.get("raw")
    if not isinstance(raw, dict):
        return {}
    return {normalize_text(str(key)): value for key, value in raw.items()}


def _value(row: dict[str, Any], keys: tuple[str, ...]) -> str:
    raw = _raw_map(row)
    for key in keys:
        direct = row.get(key)
        if direct not in (None, ""):
            return str(direct).strip()
        nested = raw.get(normalize_text(key))
        if nested not in (None, ""):
            return str(nested).strip()
    return ""


def _float(value: Any, default: float) -> float:
    try:
        return max(0.0, min(1.0, float(str(value).replace(",", "."))))
    except (TypeError, ValueError):
        return default


def _datetime(value: str) -> datetime | None:
    if not value:
        return None
    cleaned = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(cleaned)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


@dataclass(frozen=True)
class EvidenceOrigin:
    source_system: str
    source_event_key: str
    independence_key: str
    evidence_key: str
    source_quality: float
    source_accuracy: float
    observed_at: datetime | None


def source_system(row: dict[str, Any], source_kind: str) -> str:
    explicit = _value(row, _SOURCE_SYSTEM_KEYS)
    return normalize_text(explicit) or normalize_text(source_kind) or "unknown"


def source_event_key(row: dict[str, Any], source_kind: str) -> str:
    explicit = _value(row, _EVENT_KEYS)
    system = source_system(row, source_kind)
    if explicit:
        return sha256_parts([system, normalize_text(explicit)])
    raw = _raw_map(row)
    stable_origin = {
        key: value
        for key, value in raw.items()
        if key not in {"имя файла", "file name", "filename", "путь", "path"}
    }
    if stable_origin:
        return sha256_parts([system, stable_json(stable_origin)])
    return sha256_parts(
        [
            system,
            normalize_text(str(row.get("record_number", ""))),
            normalize_text(str(row.get("text", ""))),
            normalize_text(str(row.get("locality", ""))),
            normalize_text(str(row.get("district", ""))),
            normalize_text(str(row.get("settlement", ""))),
            normalize_text(str(row.get("street", ""))),
            normalize_text(str(row.get("res", ""))),
        ]
    )


def evidence_origin(
    row: dict[str, Any],
    *,
    observation_key: str,
    source_kind: str,
    historical_accuracy: float = 0.5,
) -> EvidenceOrigin:
    system = source_system(row, source_kind)
    event_key = source_event_key(row, source_kind)
    independence_key = sha256_parts([system, event_key])
    quality = _float(
        row.get("source_quality", _raw_map(row).get("качество источника")),
        _QUALITY_BY_KIND.get(source_kind, 0.6),
    )
    accuracy = _float(
        row.get("source_accuracy", _raw_map(row).get("точность источника")),
        historical_accuracy,
    )
    return EvidenceOrigin(
        source_system=system,
        source_event_key=event_key,
        independence_key=independence_key,
        evidence_key=sha256_parts([observation_key, independence_key]),
        source_quality=quality,
        source_accuracy=accuracy,
        observed_at=_datetime(_value(row, _DATE_KEYS)),
    )


def registry_fingerprint(rows: list[dict[str, Any]], source_kind: str) -> str:
    event_keys = sorted({source_event_key(row, source_kind) for row in rows})
    return sha256_parts([normalize_text(source_kind), *event_keys])
