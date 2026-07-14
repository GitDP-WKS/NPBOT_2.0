from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select

from .db import get_engine, initialize_database
from .domain_schema import conditional_rules, mapping_explanations, recalculation_log
from .schema import address_mappings, addresses


def mapping_explanation(mapping_id: int) -> dict[str, Any] | None:
    initialize_database()
    query = (
        select(
            address_mappings.c.id.label("mapping_id"),
            address_mappings.c.res_name,
            address_mappings.c.branch_name,
            address_mappings.c.status,
            address_mappings.c.source_confidence,
            addresses.c.address_key,
            addresses.c.locality,
            addresses.c.district,
            addresses.c.settlement,
            addresses.c.street,
            mapping_explanations.c.generation_id,
            mapping_explanations.c.confidence,
            mapping_explanations.c.explanation_json,
            mapping_explanations.c.created_at,
        )
        .select_from(
            address_mappings.join(addresses, address_mappings.c.address_id == addresses.c.id).outerjoin(
                mapping_explanations,
                mapping_explanations.c.mapping_id == address_mappings.c.id,
            )
        )
        .where(address_mappings.c.id == mapping_id)
        .order_by(mapping_explanations.c.id.desc())
        .limit(1)
    )
    with get_engine().connect() as conn:
        row = conn.execute(query).first()
    if not row:
        return None
    item = dict(row._mapping)
    try:
        explanation = json.loads(str(item.pop("explanation_json", "{}") or "{}"))
    except json.JSONDecodeError:
        explanation = {}
    item["explanation"] = explanation
    return item


def active_conditional_rules(ambiguity_key: str = "", limit: int = 500) -> list[dict[str, Any]]:
    initialize_database()
    query = (
        select(conditional_rules)
        .where(conditional_rules.c.status == "active")
        .order_by(conditional_rules.c.ambiguity_key, conditional_rules.c.rule_key)
        .limit(limit)
    )
    if ambiguity_key:
        query = query.where(conditional_rules.c.ambiguity_key == ambiguity_key)
    result: list[dict[str, Any]] = []
    with get_engine().connect() as conn:
        for row in conn.execute(query):
            item = dict(row._mapping)
            for field in ("condition_json", "result_json"):
                try:
                    item[field.removesuffix("_json")] = json.loads(str(item.pop(field)) or "{}")
                except json.JSONDecodeError:
                    item[field.removesuffix("_json")] = {}
            result.append(item)
    return result


def recent_recalculations(limit: int = 200) -> list[dict[str, Any]]:
    initialize_database()
    with get_engine().connect() as conn:
        rows = conn.execute(
            select(recalculation_log).order_by(recalculation_log.c.id.desc()).limit(limit)
        ).all()
    return [dict(row._mapping) for row in rows]
