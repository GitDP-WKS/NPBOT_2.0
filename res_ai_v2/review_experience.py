from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ReviewView:
    question: str
    explanation: str
    address_line: str
    options: list[dict[str, str]]
    allow_multiple: bool
    allow_address_edit: bool


def _address_line(address: dict[str, Any]) -> str:
    parts = [
        str(address.get("locality", "")).strip(),
        str(address.get("district", "")).strip(),
        str(address.get("settlement", "")).strip(),
        str(address.get("street", "")).strip(),
    ]
    return " · ".join(part for part in parts if part) or "Адрес не распознан"


def _deduplicate_options(options: list[dict[str, Any]]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in options:
        branch = str(item.get("branch", "")).strip()
        res = str(item.get("res", "")).strip()
        if not res or (branch, res) in seen:
            continue
        seen.add((branch, res))
        result.append({"branch": branch, "res": res})
    return result


def present_task(task: dict[str, Any]) -> ReviewView:
    payload = dict(task.get("payload") or {})
    address = dict(payload.get("address") or {})
    task_type = str(task.get("task_type", ""))
    options = _deduplicate_options(list(payload.get("options") or []))

    if task_type == "mapping_conflict":
        question = "Какой РЭС действительно обслуживает этот адрес?"
        explanation = "Для одного адреса в базе указаны разные исполнители. Выберите правильный вариант."
    elif task_type == "missing_context":
        question = "К какому району и РЭС относится этот адрес?"
        explanation = "Название встречается в нескольких местах. Уточните район и выберите исполнителя."
    elif task_type in {"prediction_review", "low_confidence", "model_error"}:
        question = "Правильно ли система определила исполнителя?"
        explanation = "Подтвердите предложенный РЭС или выберите другой."
    elif task_type == "unknown_address":
        question = "Какой РЭС обслуживает этот адрес?"
        explanation = "Система не смогла определить исполнителя самостоятельно."
    else:
        question = "Как правильно обработать эту запись?"
        explanation = "Проверьте адрес и выберите правильный РЭС."

    return ReviewView(
        question=question,
        explanation=explanation,
        address_line=_address_line(address),
        options=options,
        allow_multiple=bool(payload.get("allow_multiple", False)),
        allow_address_edit=bool(payload.get("allow_address_edit", True)),
    )
