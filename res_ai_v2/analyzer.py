from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import select, update

from .confidence import uniqueness_confidence
from .db import bump_data_version, get_engine, initialize_database, utcnow
from .normalize import sha256_parts
from .repositories import close_stale_tasks, create_or_update_task
from .schema import address_mappings, addresses

ANALYSIS_TASK_TYPES = {"mapping_conflict", "missing_context"}


def _row_dicts() -> list[dict[str, Any]]:
    query = select(addresses.c.id.label("address_id"), addresses.c.address_key, addresses.c.locality, addresses.c.district, addresses.c.settlement, addresses.c.street, addresses.c.locality_key, addresses.c.district_key, addresses.c.settlement_key, addresses.c.street_key, address_mappings.c.id.label("mapping_id"), address_mappings.c.res_name, address_mappings.c.branch_name, address_mappings.c.status, address_mappings.c.source_confidence, address_mappings.c.human_confirmations).select_from(addresses.join(address_mappings, addresses.c.id == address_mappings.c.address_id)).where(address_mappings.c.active.is_(True))
    with get_engine().connect() as conn:
        return [dict(row._mapping) for row in conn.execute(query)]


def _anchor(row: dict[str, Any]) -> tuple[str, str]:
    return ("settlement", str(row["settlement_key"])) if row.get("settlement_key") else ("locality", str(row.get("locality_key", "")))


def analyze_database() -> dict[str, int]:
    initialize_database()
    rows = _row_dicts()
    by_address: dict[str, list[dict[str, Any]]] = defaultdict(list)
    contexts_by_anchor: dict[tuple[str, str], set[tuple[str, str, str]]] = defaultdict(set)
    rows_by_anchor: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_address[str(row["address_key"])].append(row)
        anchor = _anchor(row)
        if anchor[1]:
            contexts_by_anchor[anchor].add((str(row.get("district_key", "")), str(row.get("locality_key", "")), str(row.get("settlement_key", ""))))
            rows_by_anchor[anchor].append(row)

    keep_keys: set[str] = set(); pending_tasks: list[dict[str, Any]] = []
    conflicts = missing_context = consistent = 0
    with get_engine().begin() as conn:
        for address_key, group in by_address.items():
            executors = {(row["branch_name"], row["res_name"]) for row in group}
            if len(executors) > 1:
                conflicts += 1
                mapping_ids = [int(row["mapping_id"]) for row in group]
                conn.execute(update(address_mappings).where(address_mappings.c.id.in_(mapping_ids)).values(status="conflict", updated_at=utcnow()))
                task_key = sha256_parts(["mapping_conflict", address_key]); keep_keys.add(task_key)
                pending_tasks.append({"task_key": task_key, "task_type": "mapping_conflict", "subject_type": "address", "subject_key": address_key, "title": "Один полный адрес связан с разными РЭС", "payload": {"address": {"address_key": address_key, "locality": group[0]["locality"], "district": group[0]["district"], "settlement": group[0]["settlement"], "street": group[0]["street"]}, "options": [{"mapping_id": item["mapping_id"], "branch": item["branch_name"], "res": item["res_name"], "status": item["status"], "source_confidence": item["source_confidence"]} for item in group], "allow_multiple": True, "allow_address_edit": True}, "priority": 100})
                continue
            row = group[0]; context_count = len(contexts_by_anchor.get(_anchor(row), set())) if _anchor(row)[1] else 1
            confidence = uniqueness_confidence(context_count); mapping_ids = [int(item["mapping_id"]) for item in group]
            preserved = [item for item in group if item["status"] in {"human_verified", "geo_verified"}]
            status = preserved[0]["status"] if preserved else "consistent"
            if preserved: confidence = 100.0
            conn.execute(update(address_mappings).where(address_mappings.c.id.in_(mapping_ids)).values(status=status, source_confidence=confidence, updated_at=utcnow()))
            consistent += len(group)

    for anchor, contexts in contexts_by_anchor.items():
        if len(contexts) <= 1: continue
        anchor_rows = rows_by_anchor[anchor]
        for row in [item for item in anchor_rows if not item.get("district_key")]:
            missing_context += 1
            task_key = sha256_parts(["missing_context", row["address_key"], row["res_name"]]); keep_keys.add(task_key)
            candidates = {}
            for option in anchor_rows:
                if not option.get("district_key"): continue
                key = (str(option["district"]), str(option["branch_name"]), str(option["res_name"]))
                candidates[key] = {"district": option["district"], "locality": option["locality"], "settlement": option["settlement"], "street": option["street"], "branch": option["branch_name"], "res": option["res_name"]}
            pending_tasks.append({"task_key": task_key, "task_type": "missing_context", "subject_type": "mapping", "subject_key": str(row["mapping_id"]), "title": "Для одинакового названия не указан район", "payload": {"mapping_id": row["mapping_id"], "address": {"address_key": row["address_key"], "locality": row["locality"], "district": row["district"], "settlement": row["settlement"], "street": row["street"]}, "current": {"branch": row["branch_name"], "res": row["res_name"]}, "options": list(candidates.values()), "allow_multiple": False, "allow_address_edit": True}, "priority": 80})

    for task in pending_tasks: create_or_update_task(**task)
    stale = close_stale_tasks(ANALYSIS_TASK_TYPES, keep_keys); bump_data_version()
    return {"rows": len(rows), "consistent": consistent, "conflicts": conflicts, "missing_context": missing_context, "tasks": len(keep_keys), "stale_closed": stale}
