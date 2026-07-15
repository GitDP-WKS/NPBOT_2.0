from __future__ import annotations

from typing import Any

from .structure import CURRENT_STRUCTURE

DECISION_TYPES = {
    "confirmed",
    "selected_other",
    "both_by_district",
    "both_by_condition",
    "conditional",
    "insufficient_data",
    "source_error",
    "skip",
}
CONDITIONAL_TYPES = {"both_by_district", "both_by_condition", "conditional"}
NO_SELECTION_TYPES = {"insufficient_data", "source_error", "skip"}


def normalize_review_selection(selection: dict[str, Any]) -> dict[str, Any]:
    result = dict(selection)
    selected = [
        str(value)
        for value in result.get("selected_res", [])
        if str(value) in CURRENT_STRUCTURE
    ]
    result["selected_res"] = list(dict.fromkeys(selected))
    decision_type = str(result.get("decision_type", "")).strip()
    if not decision_type:
        decision_type = "confirmed" if len(result["selected_res"]) <= 1 else "conditional"
    if decision_type not in DECISION_TYPES:
        raise ValueError("Неизвестный вариант решения проверяющего.")
    result["decision_type"] = decision_type

    if decision_type in NO_SELECTION_TYPES:
        result["selected_res"] = []
    elif not result["selected_res"]:
        raise ValueError("Для выбранного решения необходимо указать РЭС.")

    conditions = []
    for item in result.get("conditions", []):
        if not isinstance(item, dict):
            continue
        res_name = str(item.get("res", ""))
        if res_name not in CURRENT_STRUCTURE:
            continue
        condition = {
            str(key): value
            for key, value in dict(item.get("condition") or {}).items()
            if value not in (None, "")
        }
        if condition:
            conditions.append({"res": res_name, "condition": condition, "ambiguity_key": item.get("ambiguity_key", "")})
    result["conditions"] = conditions

    if decision_type in CONDITIONAL_TYPES:
        if len(result["selected_res"]) < 2:
            raise ValueError("Условное правило должно содержать не менее двух вариантов РЭС.")
        if len(conditions) < 2:
            raise ValueError("Для условного правила необходимо описать признаки каждого варианта.")

    result["comment"] = str(result.get("comment", "")).strip()
    return result


def is_fundamental_decision(selection: dict[str, Any]) -> bool:
    return str(selection.get("decision_type", "")) in CONDITIONAL_TYPES


def decision_label(selection: dict[str, Any]) -> str:
    labels = {
        "confirmed": "Подтвержден предложенный РЭС",
        "selected_other": "Выбран другой РЭС",
        "both_by_district": "Оба варианта верны в зависимости от района",
        "both_by_condition": "Оба варианта верны в зависимости от другого признака",
        "conditional": "Сохранено условное правило",
        "insufficient_data": "Недостаточно данных",
        "source_error": "Ошибка источника",
        "skip": "Задание пропущено",
    }
    return labels.get(str(selection.get("decision_type", "")), "Решение проверяющего")
