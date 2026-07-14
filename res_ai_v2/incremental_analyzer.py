from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import or_, select, update

from .confidence import uniqueness_confidence
from .db import bump_data_version, get_engine, initialize_database, utcnow
from .normalize import sha256_parts
from .repositories import create_or_update_task
from .schema import address_mappings, addresses, review_tasks


def _joined_query():
    return select(
        addresses.c.id.label("address_id"),
        addresses.c.address_key,
        addresses.c.locality,
        addresses.c.district,
        addresses.c.settlement,
        addresses.c.street,
        addresses.c.locality_key,
        addresses.c.district_key,
        addresses.c.settlement_key,
        addresses.c.street_key,
        address_mappings.c.id.label("mapping_id"),
        address_mappings.c.res_name,
        address_mappings.c.branch_name,
        address_mappings.c.status,
        address_mappings.c.source_confidence,
        address_mappings.c.human_confirmations,
    ).select_from(
        addresses.join(address_mappings, addresses.c.id == address_mappings.c.address_id)
    ).where(address_mappings.c.active.is_(True))


def _anchor(row: dict[str, Any]) -> tuple[str, str]:
    if row.get("settlement_key"):
        return "settlement", str(row["settlement_key"])
    return "locality", str(row.get("locality_key", ""))


def _affected_rows(address_ids: list[int]) -> list[dict[str, Any]]:
    ids = sorted({int(value) for value in address_ids if int(value) > 0})
    if not ids:
        return []
    with get_engine().connect() as conn:
        seed = [
            dict(row._mapping)
            for row in conn.execute(_joined_query().where(addresses.c.id.in_(ids)))
        ]
        locality_keys = {str(row["locality_key"]) for row in seed if row.get("locality_key")}
        settlement_keys = {str(row["settlement_key"]) for row in seed if row.get("settlement_key")}
        conditions = [addresses.c.id.in_(ids)]
        if locality_keys:
            conditions.append(addresses.c.locality_key.in_(locality_keys))
        if settlement_keys:
            conditions.append(addresses.c.settlement_key.in_(settlement_keys))
        return [
            dict(row._mapping)
            for row in conn.execute(_joined_query().where(or_(*conditions)))
        ]


def analyze_changed_addresses(address_ids: list[int]) -> dict[str, int]:
    """Пересчитывает только адреса и одинаковые названия, затронутые изменением."""
    initialize_database()
    rows = _affected_rows(address_ids)
    if not rows:
        return {
            "rows": 0,
            "consistent": 0,
            "conflicts": 0,
            "missing_context": 0,
            "tasks": 0,
        }

    by_address: dict[str, list[dict[str, Any]]] = defaultdict(list)
    contexts_by_anchor: dict[tuple[str, str], set[tuple[str, str, str]]] = defaultdict(set)
    rows_by_anchor: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_address[str(row["address_key"])].append(row)
        anchor = _anchor(row)
        if anchor[1]:
            contexts_by_anchor[anchor].add(
                (
                    str(row.get("district_key", "")),
                    str(row.get("locality_key", "")),
                    str(row.get("settlement_key", "")),
                )
            )
            rows_by_anchor[anchor].append(row)

    pending: list[dict[str, Any]] = []
    valid_task_keys: set[str] = set()
    conflicts = missing_context = consistent = 0
    now = utcnow()
    with get_engine().begin() as conn:
        for address_key, group in by_address.items():
            executors = {(row["branch_name"], row["res_name"]) for row in group}
            mapping_ids = [int(row["mapping_id"]) for row in group]
            if len(executors) > 1:
                conflicts += 1
                conn.execute(
                    update(address_mappings)
                    .where(address_mappings.c.id.in_(mapping_ids))
                    .values(status="conflict", updated_at=now)
                )
                task_key = sha256_parts(["mapping_conflict", address_key])
                valid_task_keys.add(task_key)
                pending.append(
                    {
                        "task_key": task_key,
                        "task_type": "mapping_conflict",
                        "subject_type": "address",
                        "subject_key": address_key,
                        "title": "Один полный адрес связан с разными РЭС",
                        "payload": {
                            "address": {
                                "address_key": address_key,
                                "locality": group[0]["locality"],
                                "district": group[0]["district"],
                                "settlement": group[0]["settlement"],
                                "street": group[0]["street"],
                            },
                            "options": [
                                {
                                    "mapping_id": row["mapping_id"],
                                    "branch": row["branch_name"],
                                    "res": row["res_name"],
                                    "status": row["status"],
                                    "source_confidence": row["source_confidence"],
                                }
                                for row in group
                            ],
                            "allow_multiple": True,
                            "allow_address_edit": True,
                        },
                        "priority": 100,
                    }
                )
                continue
            row = group[0]
            context_count = max(1, len(contexts_by_anchor.get(_anchor(row), set())))
            preserved = [item for item in group if item["status"] in {"human_verified", "geo_verified"}]
            status = preserved[0]["status"] if preserved else "consistent"
            confidence = 100.0 if preserved else uniqueness_confidence(context_count)
            conn.execute(
                update(address_mappings)
                .where(address_mappings.c.id.in_(mapping_ids))
                .values(status=status, source_confidence=confidence, updated_at=now)
            )
            consistent += len(group)

        for anchor, contexts in contexts_by_anchor.items():
            if len(contexts) <= 1:
                continue
            anchor_rows = rows_by_anchor[anchor]
            for row in [item for item in anchor_rows if not item.get("district_key")]:
                missing_context += 1
                task_key = sha256_parts(["missing_context", row["address_key"], row["res_name"]])
                valid_task_keys.add(task_key)
                candidates: dict[tuple[str, str, str], dict[str, Any]] = {}
                for option in anchor_rows:
                    if not option.get("district_key"):
                        continue
                    key = (
                        str(option["district"]),
                        str(option["branch_name"]),
                        str(option["res_name"]),
                    )
                    candidates[key] = {
                        "district": option["district"],
                        "locality": option["locality"],
                        "settlement": option["settlement"],
                        "street": option["street"],
                        "branch": option["branch_name"],
                        "res": option["res_name"],
                    }
                pending.append(
                    {
                        "task_key": task_key,
                        "task_type": "missing_context",
                        "subject_type": "mapping",
                        "subject_key": str(row["mapping_id"]),
                        "title": "Для одинакового названия не указан район",
                        "payload": {
                            "mapping_id": row["mapping_id"],
                            "address": {
                                "address_key": row["address_key"],
                                "locality": row["locality"],
                                "district": row["district"],
                                "settlement": row["settlement"],
                                "street": row["street"],
                            },
                            "current": {
                                "branch": row["branch_name"],
                                "res": row["res_name"],
                            },
                            "options": list(candidates.values()),
                            "allow_multiple": False,
                            "allow_address_edit": True,
                        },
                        "priority": 80,
                    }
                )

        affected_subjects = {str(row["address_key"]) for row in rows}
        open_rows = conn.execute(
            select(review_tasks.c.id, review_tasks.c.task_key, review_tasks.c.subject_key)
            .where(
                review_tasks.c.status == "open",
                review_tasks.c.task_type.in_(["mapping_conflict", "missing_context"]),
            )
        ).all()
        stale_ids = [
            int(item.id)
            for item in open_rows
            if str(item.subject_key) in affected_subjects
            and str(item.task_key) not in valid_task_keys
        ]
        if stale_ids:
            conn.execute(
                update(review_tasks)
                .where(review_tasks.c.id.in_(stale_ids))
                .values(status="cancelled", updated_at=now)
            )

    for task in pending:
        create_or_update_task(**task)
    bump_data_version()
    return {
        "rows": len(rows),
        "consistent": consistent,
        "conflicts": conflicts,
        "missing_context": missing_context,
        "tasks": len(valid_task_keys),
    }
