from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import insert, select

from .confidence import decision_confidence
from .config import load_settings
from .db import get_engine, initialize_database, utcnow
from .modeling import predict_with_model
from .normalize import normalize_text, sha256_parts, stable_json, whole_phrase_in
from .repositories import create_or_update_task, lookup_query_rule
from .schema import address_mappings, addresses, prediction_events
from .structure import CURRENT_STRUCTURE


@dataclass
class PredictionResult:
    status: str
    confidence: float
    method: str
    reason: str
    candidates: list[dict[str, Any]] = field(default_factory=list)
    needs_review: bool = False
    model_version: str | None = None

    @property
    def final(self) -> bool:
        return self.status == "final"


def load_search_index() -> list[dict[str, Any]]:
    initialize_database()
    query = select(addresses.c.address_key, addresses.c.locality, addresses.c.district, addresses.c.settlement, addresses.c.street, addresses.c.locality_key, addresses.c.district_key, addresses.c.settlement_key, addresses.c.street_key, address_mappings.c.res_name, address_mappings.c.branch_name, address_mappings.c.status, address_mappings.c.source_confidence, address_mappings.c.human_confirmations).select_from(addresses.join(address_mappings, addresses.c.id == address_mappings.c.address_id)).where(address_mappings.c.active.is_(True), address_mappings.c.status.not_in(["rejected", "conflict"]))
    with get_engine().connect() as conn:
        return [dict(row._mapping) for row in conn.execute(query)]


def _candidate_score(query: str, row: dict[str, Any]) -> tuple[int, dict[str, bool]]:
    matches = {"locality": bool(row.get("locality_key") and whole_phrase_in(str(row["locality_key"]), query)), "district": bool(row.get("district_key") and whole_phrase_in(str(row["district_key"]), query)), "settlement": bool(row.get("settlement_key") and whole_phrase_in(str(row["settlement_key"]), query)), "street": bool(row.get("street_key") and whole_phrase_in(str(row["street_key"]), query))}
    if not (matches["locality"] or matches["settlement"]): return 0, matches
    score = (65 if matches["locality"] else 0) + (70 if matches["settlement"] else 0) + (35 if matches["district"] else 0) + (20 if matches["street"] else 0)
    if row.get("status") == "human_verified": score += 3
    return score, matches


def _dedupe(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique = {}
    for row in values:
        key = tuple(str(row.get(name, "")) for name in ("branch", "res", "locality", "district", "settlement", "street")); unique[key] = row
    return list(unique.values())


def _address_candidates(text: str, index: list[dict[str, Any]]) -> list[dict[str, Any]]:
    query = normalize_text(text); scored = []
    for row in index:
        score, matches = _candidate_score(query, row)
        if not score: continue
        item = dict(row); item["score"], item["matches"] = score, matches; item["branch"] = item.pop("branch_name"); item["res"] = item.pop("res_name"); scored.append(item)
    scored.sort(key=lambda row: (-int(row["score"]), -int(row.get("human_confirmations", 0)), str(row.get("district", "")), str(row.get("res", ""))))
    return _dedupe(scored)


def _review_payload(text: str, candidates: list[dict[str, Any]], confidence: float, reason: str) -> dict[str, Any]:
    top = candidates[0] if candidates else {}
    return {"query_text": text, "address": {key: top.get(key, "") for key in ("locality", "district", "settlement", "street")}, "confidence": round(confidence, 1), "reason": reason, "options": [{"branch": row.get("branch", ""), "res": row.get("res", ""), "locality": row.get("locality", ""), "district": row.get("district", ""), "settlement": row.get("settlement", ""), "street": row.get("street", "")} for row in candidates[:10] if row.get("res") in CURRENT_STRUCTURE], "allow_multiple": True, "allow_address_edit": True}


def enqueue_prediction(text: str, result: PredictionResult) -> None:
    task_type = "unknown_address" if result.status == "not_found" else "prediction_review"; task_key = sha256_parts([task_type, normalize_text(text)])
    create_or_update_task(task_key=task_key, task_type=task_type, subject_type="query", subject_key=normalize_text(text), title="Неизвестный адрес" if task_type == "unknown_address" else "Проверка результата определения", payload=_review_payload(text, result.candidates, result.confidence, result.reason), priority=95 if task_type == "unknown_address" else 75)


def _record_prediction(text: str, result: PredictionResult) -> None:
    with get_engine().begin() as conn:
        conn.execute(insert(prediction_events).values(query_text=text, normalized_query=normalize_text(text), result_json=stable_json({"status": result.status, "method": result.method, "reason": result.reason, "candidates": result.candidates[:10]}), confidence=result.confidence, decision_status=result.status, model_version=result.model_version, created_at=utcnow()))


def predict(text: str, index: list[dict[str, Any]] | None = None, enqueue: bool = True) -> PredictionResult:
    initialize_database()
    if not normalize_text(text): return PredictionResult("not_found", 0.0, "empty", "Введите адрес или текст.")
    human_rule = lookup_query_rule(text)
    if human_rule is not None:
        candidates = [{"branch": item.get("branch", ""), "res": item.get("res", ""), "locality": item.get("locality", ""), "district": item.get("district", ""), "settlement": item.get("settlement", ""), "street": item.get("street", "")} for item in human_rule]
        result = PredictionResult("final", 100.0, "human_rule", "Результат ранее подтвержден человеком.", candidates, False); _record_prediction(text, result); return result

    ranked = _address_candidates(text, index if index is not None else load_search_index())
    if ranked:
        top_score = int(ranked[0]["score"]); top = [row for row in ranked if int(row["score"]) == top_score]; executors = {(row["branch"], row["res"]) for row in top}; ambiguous = len(executors) > 1
        top_row, matches = top[0], top[0]["matches"]; verified = top_row.get("status") == "human_verified"
        confidence = decision_confidence(mapping_confidence=float(top_row.get("source_confidence", 80.0)), matched_district=bool(matches.get("district")), matched_street=bool(matches.get("street")), matched_settlement=bool(matches.get("settlement")), ambiguous=ambiguous, human_verified=verified)
        if ambiguous: result = PredictionResult("ambiguous", confidence, "address_rules", "Для указанного названия найдено несколько РЭС. Уточните район, СНТ или улицу.", top, True)
        else: result = PredictionResult("final", confidence, "human_mapping" if verified else "address_rules", "Точное адресное правило подтверждено человеком." if verified else "Адрес однозначен в текущем реестре и не противоречит другим данным.", top, False)
        if result.needs_review and enqueue: enqueue_prediction(text, result)
        _record_prediction(text, result); return result

    model_candidates, version = predict_with_model(text, top_n=3)
    if model_candidates:
        probability = float(model_candidates[0]["probability"]); second = float(model_candidates[1]["probability"]) if len(model_candidates) > 1 else 0.0; cfg = load_settings(); auto = probability >= cfg.model_auto_threshold and probability - second >= cfg.model_margin_threshold
        candidates = [{"branch": row["branch"], "res": row["res"], "locality": "", "district": "", "settlement": "", "street": "", "probability": round(float(row["probability"]) * 100, 1)} for row in model_candidates]
        result = PredictionResult("final" if auto else "preliminary", round(probability * 100, 1), "model", "Модель дала устойчивый результат с большим отрывом от следующего варианта." if auto else "Модель предлагает предварительный вариант; требуется проверка человеком.", candidates, not auto, version)
        if result.needs_review and enqueue: enqueue_prediction(text, result)
        _record_prediction(text, result); return result

    result = PredictionResult("not_found", 0.0, "none", "Адрес не найден в базе, а опубликованная модель не смогла предложить вариант.", [], True)
    if enqueue: enqueue_prediction(text, result)
    _record_prediction(text, result); return result
