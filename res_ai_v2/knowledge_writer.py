from __future__ import annotations

from typing import Any

from sqlalchemy import and_, delete, insert, or_, select, update

from .db import get_engine, utcnow
from .knowledge_plan import AGENT_TASK_TYPES, KnowledgePlan
from .normalize import sha256_parts, stable_json
from .pit_schema import pit_observations
from .review_helpers import _apply
from .schema import (
    address_aliases,
    address_mappings,
    addresses,
    mapping_evidence,
    review_decisions,
    text_examples,
)
from .task_sync import sync_review_tasks_in_connection

BATCH_SIZE = 1000
LEGACY_MATCH_BATCH = 150


def _chunks(values: list[Any], size: int = BATCH_SIZE):
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _clear_working_knowledge(conn) -> None:
    conn.execute(delete(mapping_evidence))
    conn.execute(delete(text_examples))
    conn.execute(delete(address_aliases))
    conn.execute(delete(address_mappings))
    conn.execute(delete(addresses))


def _raw_signature(row: dict[str, Any]) -> tuple[str, str, str, str]:
    return tuple(
        str(row.get(field, ""))
        for field in ("locality", "district", "settlement", "street")
    )


def _canonical_values(key: str, row: dict[str, Any], now) -> dict[str, Any]:
    return {
        "address_key": key,
        "locality": str(row.get("locality", "")),
        "district": str(row.get("district", "")),
        "settlement": str(row.get("settlement", "")),
        "street": str(row.get("street", "")),
        "locality_key": str(row.get("locality_key", "")),
        "district_key": str(row.get("district_key", "")),
        "settlement_key": str(row.get("settlement_key", "")),
        "street_key": str(row.get("street_key", "")),
        "updated_at": now,
    }


def _reuse_legacy_addresses(
    conn,
    missing: dict[str, dict[str, Any]],
    result: dict[str, int],
    now,
) -> None:
    if not missing:
        return

    existing_by_signature: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    items = list(missing.items())
    for batch in _chunks(items, LEGACY_MATCH_BATCH):
        conditions = [
            and_(
                addresses.c.locality == str(row.get("locality", "")),
                addresses.c.district == str(row.get("district", "")),
                addresses.c.settlement == str(row.get("settlement", "")),
                addresses.c.street == str(row.get("street", "")),
            )
            for _, row in batch
        ]
        for existing in conn.execute(select(addresses).where(or_(*conditions))):
            item = dict(existing._mapping)
            signature = _raw_signature(item)
            previous = existing_by_signature.get(signature)
            if previous is None or int(item["id"]) < int(previous["id"]):
                existing_by_signature[signature] = item

    used_ids = set(result.values())
    for key, row in items:
        match = existing_by_signature.get(_raw_signature(row))
        if not match:
            continue
        address_id = int(match["id"])
        if address_id in used_ids:
            continue
        conn.execute(
            update(addresses)
            .where(addresses.c.id == address_id)
            .values(**_canonical_values(key, row, now))
        )
        result[key] = address_id
        used_ids.add(address_id)


def _upsert_addresses(conn, plan: KnowledgePlan) -> dict[str, int]:
    now = utcnow()
    keys = list(plan.groups)
    result: dict[str, int] = {}
    for batch in _chunks(keys):
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

    missing = {
        key: rows[0]
        for key, rows in plan.groups.items()
        if key not in result
    }
    _reuse_legacy_addresses(conn, missing, result, now)

    inserts = []
    for key, row in missing.items():
        if key in result:
            continue
        inserts.append(
            {
                **_canonical_values(key, row, now),
                "created_at": now,
            }
        )
    for batch in _chunks(inserts):
        if batch:
            conn.execute(insert(addresses), batch)
    for batch in _chunks(keys):
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


def _replace_scope(conn, address_ids: list[int], full_rebuild: bool) -> None:
    if full_rebuild:
        _clear_working_knowledge(conn)
        return
    if not address_ids:
        return
    mapping_ids = select(address_mappings.c.id).where(
        address_mappings.c.address_id.in_(address_ids)
    )
    conn.execute(delete(mapping_evidence).where(mapping_evidence.c.mapping_id.in_(mapping_ids)))
    conn.execute(delete(text_examples).where(text_examples.c.address_id.in_(address_ids)))
    conn.execute(delete(address_mappings).where(address_mappings.c.address_id.in_(address_ids)))


def _insert_mappings(
    conn,
    plan: KnowledgePlan,
    address_ids: dict[str, int],
) -> dict[tuple[int, str], int]:
    now = utcnow()
    rows = [
        {
            "address_id": address_ids[spec.address_key],
            "res_name": spec.res_name,
            "branch_name": spec.branch_name,
            "status": spec.status,
            "source_confidence": spec.confidence,
            "human_confirmations": 0,
            "active": True,
            "created_at": now,
            "updated_at": now,
        }
        for spec in plan.mappings
    ]
    for batch in _chunks(rows):
        if batch:
            conn.execute(insert(address_mappings), batch)
    if not address_ids:
        return {}
    return {
        (int(row.address_id), str(row.res_name)): int(row.id)
        for row in conn.execute(
            select(
                address_mappings.c.id,
                address_mappings.c.address_id,
                address_mappings.c.res_name,
            ).where(address_mappings.c.address_id.in_(list(address_ids.values())))
        )
    }


def _insert_evidence_and_texts(
    conn,
    plan: KnowledgePlan,
    address_ids: dict[str, int],
    mapping_ids: dict[tuple[int, str], int],
) -> None:
    now = utcnow()
    evidence = []
    texts: dict[str, dict[str, Any]] = {}
    for spec in plan.mappings:
        address_id = address_ids[spec.address_key]
        mapping_id = mapping_ids[(address_id, spec.res_name)]
        for observation in spec.observations:
            occurrences = max(1, int(observation.get("occurrence_count", 1)))
            evidence.append(
                {
                    "mapping_id": mapping_id,
                    "source_row_id": None,
                    "evidence_type": "pit_observation",
                    "evidence_key": str(observation["observation_key"]),
                    "weight": 1.0 / occurrences,
                    "created_at": now,
                }
            )
            raw_text = str(observation.get("raw_text", "")).strip()
            if not raw_text:
                continue
            example_hash = sha256_parts(
                [str(observation.get("text_key", "")), spec.res_name]
            )
            texts[example_hash] = {
                "example_hash": example_hash,
                "source_row_id": None,
                "raw_text": raw_text,
                "normalized_text": str(observation.get("text_key", "")),
                "address_id": address_id,
                "res_name": spec.res_name,
                "branch_name": spec.branch_name,
                "status": "source_only",
                "human_confirmations": 0,
                "weight": 1.0 / occurrences,
                "created_at": now,
                "updated_at": now,
            }
    for batch in _chunks(evidence):
        if batch:
            conn.execute(insert(mapping_evidence), batch)
    for batch in _chunks(list(texts.values())):
        if batch:
            conn.execute(insert(text_examples), batch)


def _apply_directives(
    conn,
    directives: dict[str, dict[str, Any]],
    directive_keys: set[str] | None,
) -> int:
    applied = 0
    for key, item in directives.items():
        if directive_keys is not None and key not in directive_keys:
            continue
        task = {
            "id": int(item["task_id"]),
            "task_key": str(item["task_key"]),
            "task_type": str(item["task_type"]),
            "subject_type": str(item["task_subject_type"]),
            "subject_key": str(item["task_subject_key"]),
            "title": str(item["title"]),
            "payload_json": str(item["payload_json"]),
            "priority": int(item["priority"]),
        }
        before, after = _apply(
            conn,
            task,
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


def write_knowledge(
    plan: KnowledgePlan,
    directives: dict[str, dict[str, Any]],
    *,
    full_rebuild: bool,
) -> dict[str, int]:
    now = utcnow()
    with get_engine().begin() as conn:
        if full_rebuild:
            _clear_working_knowledge(conn)
        address_ids = _upsert_addresses(conn, plan)
        if not full_rebuild:
            _replace_scope(conn, list(address_ids.values()), False)
        mapping_ids = _insert_mappings(conn, plan, address_ids)
        _insert_evidence_and_texts(conn, plan, address_ids, mapping_ids)
        directives_applied = _apply_directives(
            conn,
            directives,
            None if full_rebuild else plan.directive_keys,
        )
        for batch in _chunks([int(row["id"]) for row in plan.rows]):
            if batch:
                conn.execute(
                    update(pit_observations)
                    .where(pit_observations.c.id.in_(batch))
                    .values(state="analyzed", updated_at=now)
                )
        task_result = sync_review_tasks_in_connection(
            conn,
            plan.tasks,
            task_types=AGENT_TASK_TYPES,
            keep_keys=plan.keep_keys,
            close_stale=full_rebuild,
        )
    return {
        "directives_applied": directives_applied,
        "tasks_inserted": task_result["inserted"],
        "tasks_updated": task_result["updated"],
        "tasks_closed": task_result["closed"],
    }
