from __future__ import annotations

import json
from collections import defaultdict
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, insert, or_, select, update

from .db import bump_data_version, get_engine, get_setting, initialize_database, utcnow
from .normalize import sha256_parts, stable_json
from .pit_schema import knowledge_directives, knowledge_generations, pit_observations
from .pit_store import load_observations, observation_groups
from .repositories import close_stale_tasks, create_or_update_task
from .review_helpers import _apply
from .schema import (
    address_aliases,
    address_mappings,
    addresses,
    mapping_evidence,
    review_decisions,
    review_tasks,
    text_examples,
)
from .structure import CURRENT_STRUCTURE

AGENT_TASK_TYPES = {
    "mapping_conflict",
    "missing_context",
    "duplicate_observation",
    "import_issue",
    "directive_challenge",
}
BATCH_SIZE = 500


def _chunks(values: list[Any], size: int = BATCH_SIZE):
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _trust(occurrences: int, contexts: int) -> float:
    return round(
        max(
            1.0,
            min(
                99.9,
                99.9 / max(1, occurrences),
                99.9 / max(1, contexts),
            ),
        ),
        1,
    )


def _anchor(row: dict[str, Any]) -> tuple[str, str]:
    if row.get("settlement_key"):
        return "settlement", str(row["settlement_key"])
    return "locality", str(row.get("locality_key", ""))


def _address_key(row: dict[str, Any]) -> str:
    return sha256_parts(
        [
            str(row.get("locality_key", "")),
            str(row.get("district_key", "")),
            str(row.get("settlement_key", "")),
            str(row.get("street_key", "")),
        ]
    )


def _load_scope(observation_ids: list[int] | None) -> list[dict[str, Any]]:
    with get_engine().connect() as conn:
        if not observation_ids:
            return load_observations(conn)
        changed = load_observations(conn, observation_ids)
        localities = {
            str(row.get("locality_key", ""))
            for row in changed
            if row.get("locality_key")
        }
        settlements = {
            str(row.get("settlement_key", ""))
            for row in changed
            if row.get("settlement_key")
        }
        conditions = []
        if localities:
            conditions.append(pit_observations.c.locality_key.in_(localities))
        if settlements:
            conditions.append(pit_observations.c.settlement_key.in_(settlements))
        if not conditions:
            return changed
        return [
            dict(row._mapping)
            for row in conn.execute(select(pit_observations).where(or_(*conditions)))
        ]


def _load_directives() -> dict[str, dict[str, Any]]:
    query = (
        select(
            knowledge_directives,
            review_tasks.c.task_key,
            review_tasks.c.task_type,
            review_tasks.c.subject_type.label("task_subject_type"),
            review_tasks.c.subject_key.label("task_subject_key"),
            review_tasks.c.title,
            review_tasks.c.payload_json,
            review_tasks.c.priority,
        )
        .select_from(
            knowledge_directives.join(
                review_tasks,
                knowledge_directives.c.task_id == review_tasks.c.id,
            )
        )
        .where(knowledge_directives.c.active.is_(True))
        .order_by(knowledge_directives.c.id)
    )
    result: dict[str, dict[str, Any]] = {}
    with get_engine().connect() as conn:
        for row in conn.execute(query):
            item = dict(row._mapping)
            item["selection"] = json.loads(str(item["selection_json"]) or "{}")
            result[str(item["task_key"])] = item
    return result


def _start_generation(trigger_type: str, trigger_key: str, full_rebuild: bool) -> int:
    source_version = int(get_setting("data_version", "1"))
    now = utcnow()
    with get_engine().begin() as conn:
        return int(
            conn.execute(
                insert(knowledge_generations).values(
                    generation_key=uuid4().hex,
                    status="building",
                    trigger_type=trigger_type,
                    trigger_key=trigger_key,
                    source_version=source_version,
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


def _finish_generation(generation_id: int, status: str, stats: dict[str, Any]) -> None:
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
    result: dict[str, int] = {}
    for batch in _chunks(keys, 1000):
        result.update(
            {
                str(row.address_key): int(row.id)
                for row in conn.execute(
                    select(addresses.c.id, addresses.c.address_key).where(
                        addresses.c.address_key.in_(batch)
                    )
                )
            }
        )
    missing = []
    for key, rows in groups.items():
        if key in result:
            continue
        row = rows[0]
        missing.append(
            {
                "address_key": key,
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
    for batch in _chunks(missing):
        if batch:
            conn.execute(insert(addresses), batch)
    for batch in _chunks(keys, 1000):
        result.update(
            {
                str(row.address_key): int(row.id)
                for row in conn.execute(
                    select(addresses.c.id, addresses.c.address_key).where(
                        addresses.c.address_key.in_(batch)
                    )
                )
            }
        )
    return result


def _directive_task(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(item["task_id"]),
        "task_key": str(item["task_key"]),
        "task_type": str(item["task_type"]),
        "subject_type": str(item["task_subject_type"]),
        "subject_key": str(item["task_subject_key"]),
        "title": str(item["title"]),
        "payload_json": str(item["payload_json"]),
        "priority": int(item["priority"]),
    }


def _apply_directives(conn, directives: dict[str, dict[str, Any]]) -> int:
    applied = 0
    for item in directives.values():
        before, after = _apply(
            conn,
            _directive_task(item),
            dict(item["selection"]),
            str(item["actor"]),
            1,
        )
        conn.execute(
            update(review_decisions)
            .where(
                review_decisions.c.task_id == int(item["task_id"]),
                review_decisions.c.active.is_(True),
            )
            .values(before_json=stable_json(before), after_json=stable_json(after))
        )
        applied += 1
    return applied


def _challenge_task(
    task_key: str,
    base_task: dict[str, Any],
    directive: dict[str, Any],
    current_version: int,
) -> dict[str, Any] | None:
    if current_version <= int(directive.get("source_version", 0)):
        return None
    selected = set(directive["selection"].get("selected_res", []))
    observed = {
        str(option.get("res", ""))
        for option in base_task["payload"].get("options", [])
        if option.get("res")
    }
    if observed.issubset(selected):
        return None
    signature = sha256_parts(sorted(observed | selected))
    return {
        "task_key": sha256_parts(["directive_challenge", task_key, signature]),
        "task_type": "directive_challenge",
        "subject_type": base_task["subject_type"],
        "subject_key": base_task["subject_key"],
        "title": "Новые данные противоречат прежнему решению",
        "payload": {
            **base_task["payload"],
            "previous_selection": sorted(selected),
            "allow_address_edit": True,
        },
        "priority": 110,
    }


def rebuild_knowledge(
    *,
    observation_ids: list[int] | None = None,
    full_rebuild: bool = False,
    trigger_type: str = "agent",
    trigger_key: str = "automatic",
) -> dict[str, Any]:
    """Единственная точка записи рабочей базы знаний."""
    initialize_database()
    generation_id = _start_generation(trigger_type, trigger_key, full_rebuild)
    try:
        current_version = int(get_setting("data_version", "1"))
        rows = _load_scope(None if full_rebuild else observation_ids)
        directives = _load_directives()
        valid = [
            row
            for row in rows
            if (row.get("locality_key") or row.get("settlement_key"))
            and str(row.get("res_name", "")) in CURRENT_STRUCTURE
        ]
        valid_ids = {int(row["id"]) for row in valid}
        invalid = [row for row in rows if int(row["id"]) not in valid_ids]
        groups = observation_groups(valid)
        contexts: dict[tuple[str, str], set[tuple[str, str, str]]] = defaultdict(set)
        by_anchor: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for row in valid:
            anchor = _anchor(row)
            if anchor[1]:
                contexts[anchor].add(
                    (
                        str(row.get("district_key", "")),
                        str(row.get("locality_key", "")),
                        str(row.get("settlement_key", "")),
                    )
                )
                by_anchor[anchor].append(row)

        tasks: list[dict[str, Any]] = []
        keep: set[str] = set()
        now = utcnow()
        with get_engine().begin() as conn:
            if full_rebuild:
                _clear_knowledge(conn)
            address_ids = _upsert_addresses(conn, groups)
            affected = list(address_ids.values())
            if not full_rebuild and affected:
                conn.execute(
                    delete(mapping_evidence).where(
                        mapping_evidence.c.mapping_id.in_(
                            select(address_mappings.c.id).where(
                                address_mappings.c.address_id.in_(affected)
                            )
                        )
                    )
                )
                conn.execute(delete(text_examples).where(text_examples.c.address_id.in_(affected)))
                conn.execute(delete(address_mappings).where(address_mappings.c.address_id.in_(affected)))

            mapping_rows: list[dict[str, Any]] = []
            mapping_meta: list[tuple[str, str, list[dict[str, Any]]]] = []
            for address_key, group in groups.items():
                by_res: dict[str, list[dict[str, Any]]] = defaultdict(list)
                for row in group:
                    by_res[str(row["res_name"])].append(row)
                address_id = address_ids[address_key]
                conflict = len(by_res) > 1
                if conflict:
                    task_key = sha256_parts(["mapping_conflict", address_key])
                    base = {
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
                    directive = directives.get(task_key)
                    candidate = (
                        _challenge_task(task_key, base, directive, current_version)
                        if directive
                        else base
                    )
                    if candidate:
                        keep.add(candidate["task_key"])
                        tasks.append(candidate)

                anchor = _anchor(group[0])
                context_count = len(contexts.get(anchor, set())) or 1
                for res_name, items in by_res.items():
                    occurrences = sum(int(item.get("occurrence_count", 1)) for item in items)
                    duplicate = any(int(item.get("occurrence_count", 1)) > 1 for item in items)
                    mapping_rows.append(
                        {
                            "address_id": address_id,
                            "res_name": res_name,
                            "branch_name": CURRENT_STRUCTURE[res_name],
                            "status": "conflict"
                            if conflict
                            else ("source_only" if duplicate else "consistent"),
                            "source_confidence": _trust(occurrences, context_count),
                            "human_confirmations": 0,
                            "active": True,
                            "created_at": now,
                            "updated_at": now,
                        }
                    )
                    mapping_meta.append((address_key, res_name, items))
                    if duplicate and not conflict:
                        task_key = sha256_parts(["duplicate_observation", address_key, res_name])
                        base = {
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
                        directive = directives.get(task_key)
                        candidate = (
                            _challenge_task(task_key, base, directive, current_version)
                            if directive
                            else base
                        )
                        if candidate:
                            keep.add(candidate["task_key"])
                            tasks.append(candidate)

            for batch in _chunks(mapping_rows):
                if batch:
                    conn.execute(insert(address_mappings), batch)
            mapping_ids = {
                (int(row.address_id), str(row.res_name)): int(row.id)
                for row in conn.execute(
                    select(
                        address_mappings.c.id,
                        address_mappings.c.address_id,
                        address_mappings.c.res_name,
                    ).where(address_mappings.c.address_id.in_(list(address_ids.values())))
                )
            }
            evidence_rows = []
            text_rows: dict[str, dict[str, Any]] = {}
            for address_key, res_name, items in mapping_meta:
                mapping_id = mapping_ids[(address_ids[address_key], res_name)]
                for item in items:
                    occurrence_count = max(1, int(item.get("occurrence_count", 1)))
                    evidence_rows.append(
                        {
                            "mapping_id": mapping_id,
                            "source_row_id": None,
                            "evidence_type": "pit_observation",
                            "evidence_key": str(item["observation_key"]),
                            "weight": 1.0 / occurrence_count,
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
                            "weight": 1.0 / occurrence_count,
                            "created_at": now,
                            "updated_at": now,
                        }
            for batch in _chunks(evidence_rows):
                if batch:
                    conn.execute(insert(mapping_evidence), batch)
            for batch in _chunks(list(text_rows.values())):
                if batch:
                    conn.execute(insert(text_examples), batch)

            directives_applied = _apply_directives(conn, directives)
            for batch in _chunks([int(row["id"]) for row in rows], 1000):
                conn.execute(
                    update(pit_observations)
                    .where(pit_observations.c.id.in_(batch))
                    .values(state="analyzed", updated_at=now)
                )

        for row in invalid:
            task_key = sha256_parts(["import_issue", str(row["observation_key"])])
            if task_key in directives:
                continue
            keep.add(task_key)
            tasks.append(
                {
                    "task_key": task_key,
                    "task_type": "import_issue",
                    "subject_type": "observation",
                    "subject_key": str(row["id"]),
                    "title": "Не удалось определить адрес или РЭС",
                    "payload": {
                        "observation_id": row["id"],
                        "address": {
                            "locality": row.get("locality", ""),
                            "district": row.get("district", ""),
                            "settlement": row.get("settlement", ""),
                            "street": row.get("street", ""),
                        },
                        "raw_text": row.get("raw_text", ""),
                        "options": [],
                        "allow_multiple": False,
                        "allow_address_edit": True,
                    },
                    "priority": 95,
                }
            )

        for anchor, anchor_contexts in contexts.items():
            if len(anchor_contexts) <= 1:
                continue
            for row in by_anchor[anchor]:
                if row.get("district_key"):
                    continue
                address_key = _address_key(row)
                task_key = sha256_parts(["missing_context", address_key, str(row["res_name"])])
                if task_key in directives:
                    continue
                keep.add(task_key)
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
                            "options": [
                                {
                                    "district": option.get("district", ""),
                                    "locality": option.get("locality", ""),
                                    "settlement": option.get("settlement", ""),
                                    "street": option.get("street", ""),
                                    "branch": option.get("branch_name", ""),
                                    "res": option.get("res_name", ""),
                                }
                                for option in by_anchor[anchor]
                                if option.get("district_key")
                            ],
                            "allow_multiple": False,
                            "allow_address_edit": True,
                        },
                        "priority": 90,
                    }
                )

        for task in tasks:
            create_or_update_task(**task)
        stale = close_stale_tasks(AGENT_TASK_TYPES, keep) if full_rebuild else 0
        bump_data_version()
        stats = {
            "generation_id": generation_id,
            "rows_scanned": len(rows),
            "rows_changed": len(groups) + directives_applied,
            "tasks_created": len(keep),
            "conflicts": sum(t["task_type"] == "mapping_conflict" for t in tasks),
            "duplicates": sum(t["task_type"] == "duplicate_observation" for t in tasks),
            "missing_context": sum(t["task_type"] == "missing_context" for t in tasks),
            "invalid": sum(t["task_type"] == "import_issue" for t in tasks),
            "directive_challenges": sum(t["task_type"] == "directive_challenge" for t in tasks),
            "directives_applied": directives_applied,
            "stale_closed": stale,
            "full_rebuild": full_rebuild,
        }
        _finish_generation(generation_id, "completed", stats)
        return stats
    except Exception as exc:
        _finish_generation(
            generation_id,
            "failed",
            {
                "rows_scanned": 0,
                "rows_changed": 0,
                "tasks_created": 0,
                "error": str(exc),
            },
        )
        raise
