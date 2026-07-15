from __future__ import annotations

from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.engine import Connection

from .address_domain import canonicalize_address
from .domain_ingest import _insert_claims, _upsert_canonical
from .domain_schema import canonical_observations, evidence_claims
from .normalize import sha256_parts, stable_json
from .pit_schema import pit_observations


def bootstrap_domain_metadata(
    conn: Connection,
    now: datetime,
    observation_ids: list[int] | None = None,
) -> dict[str, int]:
    query = select(pit_observations)
    if observation_ids is not None:
        ids = sorted({int(value) for value in observation_ids if int(value) > 0})
        if not ids:
            return {"canonical": 0, "evidence": 0}
        query = query.where(pit_observations.c.id.in_(ids))
    rows = [dict(row._mapping) for row in conn.execute(query)]
    if not rows:
        return {"canonical": 0, "evidence": 0}

    existing_canonical = set(
        int(value)
        for value in conn.scalars(
            select(canonical_observations.c.observation_id).where(
                canonical_observations.c.observation_id.in_([int(row["id"]) for row in rows])
            )
        )
    )
    canonical_rows = []
    claims = []
    for row in rows:
        canonical = canonicalize_address(row)
        if int(row["id"]) not in existing_canonical:
            canonical_rows.append(
                {
                    "observation_id": int(row["id"]),
                    "canonical_address_key": canonical.canonical_key,
                    "ambiguity_key": canonical.ambiguity_key,
                    "context_key": canonical.context_key,
                    "address_type": canonical.address_type,
                    "completeness": canonical.completeness,
                    "components_json": stable_json(canonical.payload()),
                    "updated_at": now,
                }
            )
        event_key = sha256_parts(
            ["legacy_working_knowledge", str(row["observation_key"])]
        )
        independence_key = sha256_parts(["legacy_working_knowledge", event_key])
        claims.append(
            {
                "evidence_key": sha256_parts(
                    [str(row["observation_key"]), independence_key]
                ),
                "observation_key": str(row["observation_key"]),
                "independence_key": independence_key,
                "source_system": "legacy_working_knowledge",
                "source_event_key": event_key,
                "source_quality": 0.55,
                "source_accuracy": 0.5,
                "observed_at": None,
                "first_source_row_id": None,
                "created_at": now,
            }
        )
    _upsert_canonical(conn, canonical_rows)
    inserted = _insert_claims(conn, claims)
    for row in rows:
        count = int(
            conn.scalar(
                select(evidence_claims.c.evidence_key).where(
                    evidence_claims.c.observation_key == str(row["observation_key"])
                ).limit(1)
            )
            is not None
        )
        if count:
            conn.execute(
                update(pit_observations)
                .where(pit_observations.c.id == int(row["id"]))
                .values(
                    occurrence_count=max(1, int(row.get("occurrence_count", 1) or 1)),
                    source_count=max(1, int(row.get("source_count", 1) or 1)),
                    updated_at=now,
                )
            )
    return {"canonical": len(canonical_rows), "evidence": len(inserted)}
