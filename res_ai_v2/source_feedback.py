from __future__ import annotations

from sqlalchemy import insert, select, update

from .db import get_engine, utcnow
from .domain_schema import evidence_claims, source_quality_history
from .pit_schema import pit_observations


def record_source_feedback(observation_ids: list[int], decision_type: str) -> int:
    if decision_type not in {"confirmed", "selected_other", "source_error"}:
        return 0
    ids = sorted({int(value) for value in observation_ids if int(value) > 0})
    if not ids:
        return 0
    with get_engine().begin() as conn:
        observation_keys = list(
            conn.scalars(
                select(pit_observations.c.observation_key).where(
                    pit_observations.c.id.in_(ids)
                )
            )
        )
        systems = sorted(
            {
                str(value)
                for value in conn.scalars(
                    select(evidence_claims.c.source_system).where(
                        evidence_claims.c.observation_key.in_(observation_keys)
                    )
                )
            }
        )
        changed = 0
        for system in systems:
            row = conn.execute(
                select(source_quality_history).where(
                    source_quality_history.c.source_system == system
                )
            ).first()
            correct = int(row.correct_count) if row else 0
            incorrect = int(row.incorrect_count) if row else 0
            if decision_type == "source_error":
                incorrect += 1
            else:
                correct += 1
            accuracy = correct / max(1, correct + incorrect)
            values = {
                "correct_count": correct,
                "incorrect_count": incorrect,
                "accuracy": accuracy,
                "updated_at": utcnow(),
            }
            if row:
                conn.execute(
                    update(source_quality_history)
                    .where(source_quality_history.c.source_system == system)
                    .values(**values)
                )
            else:
                conn.execute(
                    insert(source_quality_history).values(
                        source_system=system,
                        **values,
                    )
                )
            changed += 1
    return changed
