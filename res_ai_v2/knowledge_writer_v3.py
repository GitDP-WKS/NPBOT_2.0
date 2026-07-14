from __future__ import annotations

from typing import Any

from sqlalchemy import insert, update

from .db import get_engine, utcnow
from .domain_writer import write_domain_outputs
from .knowledge_plan import AGENT_TASK_TYPES, KnowledgePlan
from .knowledge_writer import (
    _apply_directives,
    _chunks,
    _clear_working_knowledge,
    _insert_mappings,
    _replace_scope,
    _upsert_addresses,
)
from .normalize import sha256_parts
from .pit_schema import pit_observations
from .schema import mapping_evidence, text_examples
from .task_sync import sync_review_tasks_in_connection


def _insert_explainable_evidence(
    conn,
    plan: KnowledgePlan,
    address_ids: dict[str, int],
    mapping_ids: dict[tuple[int, str], int],
) -> None:
    now = utcnow()
    evidence: list[dict[str, Any]] = []
    texts: dict[str, dict[str, Any]] = {}
    for spec in plan.mappings:
        address_id = address_ids[spec.address_key]
        mapping_id = mapping_ids[(address_id, spec.res_name)]
        for observation in spec.observations:
            independent = max(1, int(observation.get("independent_evidence_count", 1) or 1))
            quality = float(observation.get("source_quality", 0.6) or 0.6)
            evidence.append(
                {
                    "mapping_id": mapping_id,
                    "source_row_id": None,
                    "evidence_type": "independent_event",
                    "evidence_key": str(observation["observation_key"]),
                    "weight": round(min(3.0, quality * (1.0 + independent / 4.0)), 4),
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
                "weight": round(min(3.0, quality * (1.0 + independent / 4.0)), 4),
                "created_at": now,
                "updated_at": now,
            }
    for batch in _chunks(evidence):
        if batch:
            conn.execute(insert(mapping_evidence), batch)
    for batch in _chunks(list(texts.values())):
        if batch:
            conn.execute(insert(text_examples), batch)


def write_knowledge_v3(
    plan: KnowledgePlan,
    directives: dict[str, dict[str, Any]],
    *,
    generation_id: int,
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
        _insert_explainable_evidence(conn, plan, address_ids, mapping_ids)
        domain_result = write_domain_outputs(
            conn,
            plan,
            address_ids,
            mapping_ids,
            generation_id=generation_id,
            full_rebuild=full_rebuild,
        )
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
        "conditional_rules": domain_result["conditional_rules"],
        "explanations": domain_result["explanations"],
    }
