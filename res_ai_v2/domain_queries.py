from __future__ import annotations

import json
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.engine import Connection

from .domain_schema import canonical_observations, evidence_claims, source_evidence
from .pit_schema import pit_observations


def load_domain_observations(
    conn: Connection,
    observation_ids: list[int] | None = None,
) -> list[dict[str, Any]]:
    evidence_counts = (
        select(
            evidence_claims.c.observation_key.label("claim_observation_key"),
            func.count(evidence_claims.c.evidence_key).label("accepted_evidence_count"),
            func.count(func.distinct(evidence_claims.c.independence_key)).label(
                "independent_evidence_count"
            ),
            func.avg(evidence_claims.c.source_quality).label("source_quality"),
            func.avg(evidence_claims.c.source_accuracy).label("source_accuracy"),
            func.max(evidence_claims.c.observed_at).label("observed_at"),
        )
        .group_by(evidence_claims.c.observation_key)
        .subquery()
    )
    duplicate_counts = (
        select(
            source_evidence.c.observation_key.label("duplicate_observation_key"),
            func.count(source_evidence.c.source_row_id).label("technical_duplicate_count"),
        )
        .where(source_evidence.c.technical_duplicate.is_(True))
        .group_by(source_evidence.c.observation_key)
        .subquery()
    )
    query = (
        select(
            pit_observations,
            canonical_observations.c.canonical_address_key,
            canonical_observations.c.ambiguity_key,
            canonical_observations.c.context_key,
            canonical_observations.c.address_type,
            canonical_observations.c.completeness,
            canonical_observations.c.components_json,
            evidence_counts.c.accepted_evidence_count,
            evidence_counts.c.independent_evidence_count,
            evidence_counts.c.source_quality,
            evidence_counts.c.source_accuracy,
            evidence_counts.c.observed_at,
            duplicate_counts.c.technical_duplicate_count,
        )
        .select_from(
            pit_observations.outerjoin(
                canonical_observations,
                canonical_observations.c.observation_id == pit_observations.c.id,
            )
            .outerjoin(
                evidence_counts,
                evidence_counts.c.claim_observation_key == pit_observations.c.observation_key,
            )
            .outerjoin(
                duplicate_counts,
                duplicate_counts.c.duplicate_observation_key == pit_observations.c.observation_key,
            )
        )
    )
    if observation_ids is not None:
        ids = sorted({int(value) for value in observation_ids if int(value) > 0})
        if not ids:
            return []
        query = query.where(pit_observations.c.id.in_(ids))

    result: list[dict[str, Any]] = []
    for row in conn.execute(query):
        item = dict(row._mapping)
        try:
            components = json.loads(str(item.pop("components_json", "{}") or "{}"))
        except json.JSONDecodeError:
            components = {}
        for key, value in components.items():
            item.setdefault(key, value)
        item["canonical_address_key"] = str(
            item.get("canonical_address_key") or item.get("canonical_key") or ""
        )
        item["accepted_evidence_count"] = int(item.get("accepted_evidence_count") or 0)
        item["independent_evidence_count"] = int(
            item.get("independent_evidence_count") or 0
        )
        item["technical_duplicate_count"] = int(
            item.get("technical_duplicate_count") or 0
        )
        item["source_quality"] = float(item.get("source_quality") or 0.6)
        item["source_accuracy"] = float(item.get("source_accuracy") or 0.5)
        result.append(item)
    return result
