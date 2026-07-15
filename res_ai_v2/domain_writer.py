from __future__ import annotations

from typing import Any

from sqlalchemy import insert, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from .db import utcnow
from .domain_schema import conditional_rules, mapping_explanations
from .knowledge_plan import KnowledgePlan
from .normalize import stable_json

BATCH_SIZE = 1000


def _chunks(values: list[Any], size: int = BATCH_SIZE):
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _upsert_rules(conn, plan: KnowledgePlan, generation_id: int, full_rebuild: bool) -> int:
    now = utcnow()
    keep = {rule.rule_key for rule in plan.conditional_rules}
    if full_rebuild:
        conn.execute(
            update(conditional_rules)
            .where(conditional_rules.c.status == "active")
            .values(status="inactive", updated_at=now)
        )
    rows = [
        {
            "rule_key": rule.rule_key,
            "ambiguity_key": rule.ambiguity_key,
            "condition_json": stable_json(rule.condition),
            "result_json": stable_json(rule.result),
            "status": "active",
            "generation_id": generation_id,
            "created_at": now,
            "updated_at": now,
        }
        for rule in plan.conditional_rules
    ]
    for batch in _chunks(rows):
        if not batch:
            continue
        if conn.dialect.name == "postgresql":
            statement = postgresql_insert(conditional_rules).values(batch)
            statement = statement.on_conflict_do_update(
                index_elements=[conditional_rules.c.rule_key],
                set_={
                    "ambiguity_key": statement.excluded.ambiguity_key,
                    "condition_json": statement.excluded.condition_json,
                    "result_json": statement.excluded.result_json,
                    "status": "active",
                    "generation_id": generation_id,
                    "updated_at": now,
                },
            )
            conn.execute(statement)
        elif conn.dialect.name == "sqlite":
            statement = sqlite_insert(conditional_rules).values(batch)
            statement = statement.on_conflict_do_update(
                index_elements=[conditional_rules.c.rule_key],
                set_={
                    "ambiguity_key": statement.excluded.ambiguity_key,
                    "condition_json": statement.excluded.condition_json,
                    "result_json": statement.excluded.result_json,
                    "status": "active",
                    "generation_id": generation_id,
                    "updated_at": now,
                },
            )
            conn.execute(statement)
        else:
            for row in batch:
                existing = conn.scalar(
                    select(conditional_rules.c.rule_key).where(
                        conditional_rules.c.rule_key == row["rule_key"]
                    )
                )
                if existing:
                    conn.execute(
                        update(conditional_rules)
                        .where(conditional_rules.c.rule_key == row["rule_key"])
                        .values(**{key: value for key, value in row.items() if key != "rule_key"})
                    )
                else:
                    conn.execute(insert(conditional_rules).values(**row))
    if not full_rebuild and keep:
        conn.execute(
            update(conditional_rules)
            .where(
                conditional_rules.c.ambiguity_key.in_(
                    {rule.ambiguity_key for rule in plan.conditional_rules}
                ),
                conditional_rules.c.rule_key.not_in(sorted(keep)),
                conditional_rules.c.status == "active",
            )
            .values(status="inactive", updated_at=now)
        )
    return len(rows)


def _save_explanations(
    conn,
    plan: KnowledgePlan,
    address_ids: dict[str, int],
    mapping_ids: dict[tuple[int, str], int],
    generation_id: int,
) -> int:
    now = utcnow()
    rows = []
    for spec in plan.mappings:
        address_id = address_ids[spec.address_key]
        mapping_id = mapping_ids[(address_id, spec.res_name)]
        rows.append(
            {
                "mapping_id": mapping_id,
                "generation_id": generation_id,
                "confidence": spec.confidence,
                "explanation_json": stable_json(spec.explanation),
                "created_at": now,
            }
        )
    for batch in _chunks(rows):
        if batch:
            conn.execute(insert(mapping_explanations), batch)
    return len(rows)


def write_domain_outputs(
    conn,
    plan: KnowledgePlan,
    address_ids: dict[str, int],
    mapping_ids: dict[tuple[int, str], int],
    *,
    generation_id: int,
    full_rebuild: bool,
) -> dict[str, int]:
    return {
        "conditional_rules": _upsert_rules(conn, plan, generation_id, full_rebuild),
        "explanations": _save_explanations(
            conn,
            plan,
            address_ids,
            mapping_ids,
            generation_id,
        ),
    }
