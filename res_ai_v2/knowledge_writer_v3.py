from __future__ import annotations

from typing import Any

from sqlalchemy import insert, update

from .db import get_engine, utcnow
from .domain_writer import write_domain_outputs
from .knowledge_plan import AGENT_TASK_TYPES, KnowledgePlan
from .knowledge_writer import (
    _chunks,
    _clear_working_knowledge,
    _insert_mappings,
    _replace_scope,
    _upsert_addresses,
)
from .normalize import sha256_parts, stable_json
from .pit_schema import pit_observations
from .review_helpers import _apply
from .review_policy import CONDITIONAL_TYPES, NO_SELECTION_TYPES
from .schema import mapping_evidence, review_decisions, text_examples
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


def _apply_directives_v3(
    conn,
    directives: dict[str, dict[str, Any]],
    directive_keys: set[str] | None,
) -> int:
    applied = 0
    for key, item in directives.items():
        if directive_keys is not None and key not in directive_keys:
            continue
        selection = dict(item["selection"])
        decision_type = str(selection.get("decision_type", "confirmed"))
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
        if decision_type in CONDITIONAL_TYPES | NO_SELECTION_TYPES:
            before = {"directive_only": True}
            after = {
                "decision_type": decision_type,
                "selected_res": selection.get("selected_res", []),
                "conditions": selection.get("conditions", []),
            }
        else:
            before, after = _apply(
                conn,
                task,
                selection,
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
        directives_applied = _apply_directives_v3(
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
