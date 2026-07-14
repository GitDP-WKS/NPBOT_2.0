from __future__ import annotations

from collections import defaultdict
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, insert, or_, select, update

from .db import bump_data_version, get_engine, get_setting, initialize_database, utcnow
from .normalize import sha256_parts, stable_json
from .pit_schema import knowledge_generations, pit_observations
from .pit_store import load_observations, observation_groups
from .repositories import close_stale_tasks, create_or_update_task
from .schema import (
    address_aliases,
    address_mappings,
    addresses,
    mapping_evidence,
    text_examples,
)
from .structure import CURRENT_STRUCTURE

AGENT_TASK_TYPES = {"mapping_conflict", "missing_context", "duplicate_observation"}
BATCH_SIZE = 500


def _chunks(values: list[Any], size: int = BATCH_SIZE):
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _trust(occurrences: int, contexts: int) -> float:
    occurrence_score = 99.9 / max(1, occurrences)
    context_score = 99.9 / max(1, contexts)
    return round(max(1.0, min(99.9, occurrence_score, context_score)), 1)


def _anchor(row: dict[str, Any]) -> tuple[str, str]:
    if row.get("settlement_key"):
        return "settlement", str(row["settlement_key"])
    return "locality", str(row.get("locality_key", ""))


def _load_scope(observation_ids: list[int] | None) -> list[dict[str, Any]]:
    with get_engine().connect() as conn:
        if not observation_ids:
            return load_observations(conn)
        changed = load_observations(conn, observation_ids)
        locality_keys = {str(row.get("locality_key", "")) for row in changed if row.get("locality_key")}
        settlement_keys = {
            str(row.get("settlement_key", "")) for row in changed if row.get("settlement_key")
        }
        conditions = []
        if locality_keys:
            conditions.append(pit_observations.c.locality_key.in_(locality_keys))
        if settlement_keys:
            conditions.append(pit_observations.c.settlement_key.in_(settlement_keys))
        if not conditions:
            return changed
        query = select(pit_observations).where(or_(*conditions))
        return [dict(row._mapping) for row in conn.execute(query)]


def _start_generation(trigger_type: str, trigger_key: str, full_rebuild: bool) -> int:
    now = utcnow()
    with get_engine().begin() as conn:
        return int(
            conn.execute(
                insert(knowledge_generations).values(
                    generation_key=uuid4().hex,
                    status="building",
                    trigger_type=trigger_type,
                    trigger_key=trigger_key,
                    source_version=int(get_setting("data_version", "1")),
                    full_rebuild=full_rebuild,
                    rows_scanned=0,
                    rows_changed=0,
                    tasks_created=0,
                    stats_json="{}",
                    started_at=now,
                    finished_at=None,
                )
            ).inserted_primary_key[0]
        )


def _finish_generation(generation_id: int, *, status: str, stats: dict[str, Any]) -> None:
    with get_engine().begin() as conn:
        conn.execute(
            update(knowledge_generations)
            .where(knowledge_generations.c.id == generation_id)
            .values(
                status=status,
                rows_scanned=int(stats.get("rows_scanned", 0)),
                rows_changed=int(stats.get("rows_changed", 0)),
                tasks_created=int(stats.get("tasks_created", 0)),
                stats_json=stable_json(stats),
                finished_at=utcnow(),
            )
        )


def _clear_knowledge(conn) -> None:
    conn.execute(delete(mapping_evidence))
    conn.execute(delete(text_examples))
    conn.execute(delete(address_aliases))
    conn.execute(delete(address_mappings))
    conn.execute(delete(addresses))


def _upsert_addresses(conn, groups: dict[str, list[dict[str, Any]]]) -> dict[str, int]:
    now = utcnow()
    keys = list(groups)
    existing: dict[str, int] = {}
    for batch in _chunks(keys, 1000):
        existing.update(
            {
                str(item.address_key): int(item.id)
                for item in conn.execute(
                    select(addresses.c.id, addresses.c.address_key).where(
                        addresses.c.address_key.in_(batch)
                    )
                )
            }
        )
    payload = []
    for address_key, rows in groups.items():
        if address_key in existing:
            continue
        row = rows[0]
        payload.append(
            {
                "address_key": address_key,
                "locality": str(row.get("locality", "")),
                "district": str(row.get("district", "")),
                "settlement": str(row.get("settlement", "")),
                "street": str(row.get("street", "")),
                "locality_key": str(row.get("locality_key", "")),
                "district_key": str(row.get("district_key", "")),
                "settlement_key": str(row.get("settlement_key", "")),
                "street_key": str(row.get("street_key", "")),
                "created_at": now,
                "updated_at": now,
            }
        )
    for batch in _chunks(payload):
        if batch:
            conn.execute(insert(addresses), batch)
    for batch in _chunks(keys, 1000):
        existing.update(
            {
                str(item.address_key): int(item.id)
                for item in conn.execute(
                    select(addresses.c.id, addresses.c.address_key).where(
                        addresses.c.address_key.in_(batch)
                    )
                )
            }
        )
    return existing


def rebuild_knowledge(
    *,
    observation_ids: list[int] | None = None,
    full_rebuild: bool = False,
    trigger_type: str = "agent",
    trigger_key: str = "automatic",
) -> dict[str, Any]:
    """Единственная точка записи рабочей базы знаний.

    Импорт и решения людей попадают в яму/директивы. Только этот обработчик
    преобразует их в адреса, связи адрес–РЭС и обучающие тексты.
    """
    initialize_database()
    generation_id = _start_generation(trigger_type, trigger_key, full_rebuild)
    try:
        rows = _load_scope(None if full_rebuild else observation_ids)
        valid = [
            row
            for row in rows
            if (row.get("locality_key") or row.get("settlement_key"))
            and str(row.get("res_name", "")) in CURRENT_STRUCTURE
        ]
        groups = observation_groups(valid)
        contexts_by_anchor: dict[tuple[str, str], set[tuple[str, str, str]]] = defaultdict(set)
        rows_by_anchor: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for row in valid:
            anchor = _anchor(row)
            if not anchor[1]:
                continue
            contexts_by_anchor[anchor].add(
                (
                    str(row.get("district_key", "")),
                    str(row.get("locality_key", "")),
                    str(row.get("settlement_key", "")),
                )
            )
            rows_by_anchor[anchor].append(row)

        tasks: list[dict[str, Any]] = []
        keep_keys: set[str] = set()
        now = utcnow()
        changed = 0
        with get_engine().begin() as conn:
            if full_rebuild:
                _clear_knowledge(conn)
            address_ids = _upsert_addresses(conn, groups)
            affected_address_ids = list(address_ids.values())
            if not full_rebuild and affected_address_ids:
                conn.execute(
                    delete(mapping_evidence).where(
                        mapping_evidence.c.mapping_id.in_(
                            select(address_mappings.c.id).where(
                                address_mappings.c.address_id.in_(affected_address_ids)
                            )
                        )
                    )
                )
                conn.execute(
                    delete(text_examples).where(text_examples.c.address_id.in_(affected_address_ids))
                )
                conn.execute(
                    delete(address_mappings).where(
                        address_mappings.c.address_id.in_(affected_address_ids)
                    )
                )

            mapping_rows: list[dict[str, Any]] = []
            mapping_meta: list[tuple[str, str, list[dict[str, Any]], str, float]] = []
            for address_key, group in groups.items():
                by_res: dict[str, list[dict[str, Any]]] = defaultdict(list)
                for row in group:
                    by_res[str(row["res_name"])].append(row)
                address_id = address_ids[address_key]
                conflict = len(by_res) > 1
                if conflict:
                    task_key = sha256_parts(["mapping_conflict", address_key])
                    keep_keys.add(task_key)
                    tasks.append(
                        {
                            "task_key": task_key,
                            "task_type": "mapping_conflict",
                            "subject_type": "address",
                            "subject_key": address_key,
                            "title": "Один адрес связан с разными РЭС",
                            "payload": {
                                "address": {
                                    "address_key": address_key,
                                    "locality": group[0].get("locality", ""),
                                    "district": group[0].get("district", ""),
                                    "settlement": group[0].get("settlement", ""),
                                    "street": group[0].get("street", ""),
                                },
                                "options": [
                                    {
                                        "branch": CURRENT_STRUCTURE[res],
                                        "res": res,
                                        "occurrences": sum(
                                            int(item.get("occurrence_count", 1)) for item in items
                                        ),
                                    }
                                    for res, items in sorted(by_res.items())
                                ],
                                "allow_multiple": True,
                                "allow_address_edit": True,
                            },
                            "priority": 100,
                        }
                    )

                anchor = _anchor(group[0])
                contexts = len(contexts_by_anchor.get(anchor, set())) or 1
                for res_name, items in by_res.items():
                    occurrences = sum(int(item.get("occurrence_count", 1)) for item in items)
                    duplicate = any(int(item.get("occurrence_count", 1)) > 1 for item in items)
                    confidence = _trust(occurrences, contexts)
                    status = "conflict" if conflict else ("source_only" if duplicate else "consistent")
                    mapping_rows.append(
                        {
                            "address_id": address_id,
                            "res_name": res_name,
                            "branch_name": CURRENT_STRUCTURE[res_name],
                            "status": status,
                            "source_confidence": confidence,
                            "human_confirmations": 0,
                            "active": True,
                            "created_at": now,
                            "updated_at": now,
                        }
                    )
                    mapping_meta.append((address_key, res_name, items, status, confidence))
                    if duplicate and not conflict:
                        task_key = sha256_parts(["duplicate_observation", address_key, res_name])
                        keep_keys.add(task_key)
                        tasks.append(
                            {
                                "task_key": task_key,
                                "task_type": "duplicate_observation",
                                "subject_type": "address",
                                "subject_key": address_key,
                                "title": "Адрес повторяется в исходных данных",
                                "payload": {
                                    "address": {
                                        "address_key": address_key,
                                        "locality": items[0].get("locality", ""),
                                        "district": items[0].get("district", ""),
                                        "settlement": items[0].get("settlement", ""),
                                        "street": items[0].get("street", ""),
                                    },
                                    "current": {
                                        "branch": CURRENT_STRUCTURE[res_name],
                                        "res": res_name,
                                    },
                                    "options": [
                                        {"branch": CURRENT_STRUCTURE[res_name], "res": res_name}
                                    ],
                                    "occurrences": occurrences,
                                    "allow_multiple": False,
                                    "allow_address_edit": True,
                                },
                                "priority": 85,
                            }
                        )

            for batch in _chunks(mapping_rows):
                if batch:
                    conn.execute(insert(address_mappings), batch)
            mapping_ids = {
                (int(item.address_id), str(item.res_name)): int(item.id)
                for item in conn.execute(
                    select(
                        address_mappings.c.id,
                        address_mappings.c.address_id,
                        address_mappings.c.res_name,
                    ).where(address_mappings.c.address_id.in_(list(address_ids.values())))
                )
            }

            evidence_rows = []
            text_rows: dict[str, dict[str, Any]] = {}
            for address_key, res_name, items, _, _ in mapping_meta:
                mapping_id = mapping_ids[(address_ids[address_key], res_name)]
                for item in items:
                    evidence_rows.append(
                        {
                            "mapping_id": mapping_id,
                            "source_row_id": None,
                            "evidence_type": "pit_observation",
                            "evidence_key": str(item["observation_key"]),
                            "weight": 1.0 / max(1, int(item.get("occurrence_count", 1))),
                            "created_at": now,
                        }
                    )
                    raw_text = str(item.get("raw_text", "")).strip()
                    if raw_text:
                        example_hash = sha256_parts([str(item.get("text_key", "")), res_name])
                        text_rows[example_hash] = {
                            "example_hash": example_hash,
                            "source_row_id": None,
                            "raw_text": raw_text,
                            "normalized_text": str(item.get("text_key", "")),
                            "address_id": address_ids[address_key],
                            "res_name": res_name,
                            "branch_name": CURRENT_STRUCTURE[res_name],
                            "status": "source_only",
                            "human_confirmations": 0,
                            "weight": 1.0 / max(1, int(item.get("occurrence_count", 1))),
                            "created_at": now,
                            "updated_at": now,
                        }
            for batch in _chunks(evidence_rows):
                if batch:
                    conn.execute(insert(mapping_evidence), batch)
            for batch in _chunks(list(text_rows.values())):
                if batch:
                    conn.execute(insert(text_examples), batch)

            for row in valid:
                conn.execute(
                    update(pit_observations)
                    .where(pit_observations.c.id == int(row["id"]))
                    .values(state="analyzed", updated_at=now)
                )
            changed = len(mapping_rows)

        for anchor, contexts in contexts_by_anchor.items():
            if len(contexts) <= 1:
                continue
            for row in rows_by_anchor[anchor]:
                if row.get("district_key"):
                    continue
                address_key = sha256_parts(
                    [
                        str(row.get("locality_key", "")),
                        str(row.get("district_key", "")),
                        str(row.get("settlement_key", "")),
                        str(row.get("street_key", "")),
                    ]
                )
                task_key = sha256_parts(["missing_context", address_key, str(row["res_name"])])
                keep_keys.add(task_key)
                candidates = []
                for option in rows_by_anchor[anchor]:
                    if not option.get("district_key"):
                        continue
                    candidates.append(
                        {
                            "district": option.get("district", ""),
                            "locality": option.get("locality", ""),
                            "settlement": option.get("settlement", ""),
                            "street": option.get("street", ""),
                            "branch": option.get("branch_name", ""),
                            "res": option.get("res_name", ""),
                        }
                    )
                tasks.append(
                    {
                        "task_key": task_key,
                        "task_type": "missing_context",
                        "subject_type": "observation",
                        "subject_key": str(row["id"]),
                        "title": "Не указан район",
                        "payload": {
                            "observation_id": row["id"],
                            "address": {
                                "address_key": address_key,
                                "locality": row.get("locality", ""),
                                "district": row.get("district", ""),
                                "settlement": row.get("settlement", ""),
                                "street": row.get("street", ""),
                            },
                            "current": {
                                "branch": row.get("branch_name", ""),
                                "res": row.get("res_name", ""),
                            },
                            "options": candidates,
                            "allow_multiple": False,
                            "allow_address_edit": True,
                        },
                        "priority": 90,
                    }
                )

        for task in tasks:
            create_or_update_task(**task)
        stale_closed = close_stale_tasks(AGENT_TASK_TYPES, keep_keys) if full_rebuild else 0
        bump_data_version()
        stats = {
            "generation_id": generation_id,
            "rows_scanned": len(rows),
            "rows_changed": changed,
            "tasks_created": len(keep_keys),
            "conflicts": sum(1 for task in tasks if task["task_type"] == "mapping_conflict"),
            "duplicates": sum(
                1 for task in tasks if task["task_type"] == "duplicate_observation"
            ),
            "missing_context": sum(
                1 for task in tasks if task["task_type"] == "missing_context"
            ),
            "stale_closed": stale_closed,
            "full_rebuild": full_rebuild,
        }
        _finish_generation(generation_id, status="completed", stats=stats)
        return stats
    except Exception as exc:
        _finish_generation(
            generation_id,
            status="failed",
            stats={"rows_scanned": 0, "rows_changed": 0, "tasks_created": 0, "error": str(exc)},
        )
        raise
