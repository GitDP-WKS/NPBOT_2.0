from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.engine import Connection

from .address_domain import canonicalize_address
from .domain_schema import (
    canonical_observations,
    evidence_claims,
    source_evidence,
    source_quality_history,
    source_registries,
)
from .normalize import stable_json
from .pit_schema import pit_observations, pit_occurrences
from .pit_store import observation_key
from .provenance import evidence_origin, registry_fingerprint, source_system

BATCH_SIZE = 1000


def _chunks(values: list[Any], size: int = BATCH_SIZE):
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _insert_claims(conn: Connection, rows: list[dict[str, Any]]) -> set[str]:
    if not rows:
        return set()
    dialect = conn.dialect.name
    inserted: set[str] = set()
    for batch in _chunks(rows):
        if dialect == "postgresql":
            statement = (
                postgresql_insert(evidence_claims)
                .values(batch)
                .on_conflict_do_nothing(index_elements=[evidence_claims.c.evidence_key])
                .returning(evidence_claims.c.evidence_key)
            )
            inserted.update(str(value) for value in conn.scalars(statement))
        elif dialect == "sqlite":
            statement = (
                sqlite_insert(evidence_claims)
                .values(batch)
                .on_conflict_do_nothing(index_elements=[evidence_claims.c.evidence_key])
                .returning(evidence_claims.c.evidence_key)
            )
            inserted.update(str(value) for value in conn.scalars(statement))
        else:
            for item in batch:
                try:
                    with conn.begin_nested():
                        conn.execute(insert(evidence_claims).values(**item))
                    inserted.add(str(item["evidence_key"]))
                except IntegrityError:
                    continue
    return inserted


def _upsert_canonical(conn: Connection, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    dialect = conn.dialect.name
    unique = {int(item["observation_id"]): item for item in rows}
    values = list(unique.values())
    for batch in _chunks(values):
        if dialect == "postgresql":
            statement = postgresql_insert(canonical_observations).values(batch)
            statement = statement.on_conflict_do_update(
                index_elements=[canonical_observations.c.observation_id],
                set_={
                    "canonical_address_key": statement.excluded.canonical_address_key,
                    "ambiguity_key": statement.excluded.ambiguity_key,
                    "context_key": statement.excluded.context_key,
                    "address_type": statement.excluded.address_type,
                    "completeness": statement.excluded.completeness,
                    "components_json": statement.excluded.components_json,
                    "updated_at": statement.excluded.updated_at,
                },
            )
            conn.execute(statement)
        elif dialect == "sqlite":
            statement = sqlite_insert(canonical_observations).values(batch)
            statement = statement.on_conflict_do_update(
                index_elements=[canonical_observations.c.observation_id],
                set_={
                    "canonical_address_key": statement.excluded.canonical_address_key,
                    "ambiguity_key": statement.excluded.ambiguity_key,
                    "context_key": statement.excluded.context_key,
                    "address_type": statement.excluded.address_type,
                    "completeness": statement.excluded.completeness,
                    "components_json": statement.excluded.components_json,
                    "updated_at": statement.excluded.updated_at,
                },
            )
            conn.execute(statement)
        else:
            for item in batch:
                existing = conn.scalar(
                    select(canonical_observations.c.observation_id).where(
                        canonical_observations.c.observation_id == item["observation_id"]
                    )
                )
                if existing:
                    conn.execute(
                        update(canonical_observations)
                        .where(canonical_observations.c.observation_id == item["observation_id"])
                        .values(**{key: value for key, value in item.items() if key != "observation_id"})
                    )
                else:
                    conn.execute(insert(canonical_observations).values(**item))


def reconcile_domain_evidence(
    conn: Connection,
    *,
    source_file_id: int,
    file_hash: str,
    source_kind: str,
    rows: list[dict[str, Any]],
    source_row_ids: dict[tuple[str, int, str], int],
    now: datetime,
) -> dict[str, Any]:
    observation_keys = sorted({observation_key(row) for row in rows})
    observation_ids = {
        str(item.observation_key): int(item.id)
        for item in conn.execute(
            select(pit_observations.c.id, pit_observations.c.observation_key).where(
                pit_observations.c.observation_key.in_(observation_keys)
            )
        )
    }

    registry_system = source_system(rows[0], source_kind) if rows else source_kind
    conn.execute(
        insert(source_registries).values(
            source_file_id=source_file_id,
            file_hash=file_hash,
            registry_fingerprint=registry_fingerprint(rows, source_kind),
            source_system=registry_system,
            source_quality=0.6,
            created_at=now,
        )
    )

    history = {
        str(item.source_system): float(item.accuracy)
        for item in conn.execute(select(source_quality_history))
    }
    entries: list[dict[str, Any]] = []
    claim_by_key: dict[str, dict[str, Any]] = {}
    canonical_rows: list[dict[str, Any]] = []
    for row in rows:
        key = observation_key(row)
        observation_id = observation_ids.get(key)
        source_row_id = source_row_ids.get(
            (
                str(row.get("canonical_hash", "")),
                int(row.get("row_number", 0)),
                str(row.get("sheet_name", "")),
            )
        )
        if not observation_id or not source_row_id:
            continue
        canonical = canonicalize_address(row)
        canonical_rows.append(
            {
                "observation_id": observation_id,
                "canonical_address_key": canonical.canonical_key,
                "ambiguity_key": canonical.ambiguity_key,
                "context_key": canonical.context_key,
                "address_type": canonical.address_type,
                "completeness": canonical.completeness,
                "components_json": stable_json(canonical.payload()),
                "updated_at": now,
            }
        )
        origin = evidence_origin(
            row,
            observation_key=key,
            source_kind=source_kind,
            historical_accuracy=history.get(source_system(row, source_kind), 0.5),
        )
        claim_by_key.setdefault(
            origin.evidence_key,
            {
                "evidence_key": origin.evidence_key,
                "observation_key": key,
                "independence_key": origin.independence_key,
                "source_system": origin.source_system,
                "source_event_key": origin.source_event_key,
                "source_quality": origin.source_quality,
                "source_accuracy": origin.source_accuracy,
                "observed_at": origin.observed_at,
                "first_source_row_id": source_row_id,
                "created_at": now,
            },
        )
        entries.append(
            {
                "source_row_id": source_row_id,
                "source_file_id": source_file_id,
                "observation_key": key,
                "evidence_key": origin.evidence_key,
                "independence_key": origin.independence_key,
            }
        )

    inserted_claims = _insert_claims(conn, list(claim_by_key.values()))
    claim_owners = {
        str(item.evidence_key): int(item.first_source_row_id)
        for item in conn.execute(
            select(evidence_claims.c.evidence_key, evidence_claims.c.first_source_row_id).where(
                evidence_claims.c.evidence_key.in_(list(claim_by_key))
            )
        )
    }
    accepted_source_rows: set[int] = set()
    seen_inserted: set[str] = set()
    source_rows_payload: list[dict[str, Any]] = []
    technical_source_rows: list[int] = []
    for entry in entries:
        evidence_key = str(entry["evidence_key"])
        accepted = evidence_key in inserted_claims and evidence_key not in seen_inserted
        if accepted:
            accepted_source_rows.add(int(entry["source_row_id"]))
            seen_inserted.add(evidence_key)
        else:
            technical_source_rows.append(int(entry["source_row_id"]))
        source_rows_payload.append(
            {
                **entry,
                "technical_duplicate": not accepted,
                "duplicate_of_source_row_id": None
                if accepted
                else claim_owners.get(evidence_key),
                "created_at": now,
            }
        )
    for batch in _chunks(source_rows_payload):
        if batch:
            conn.execute(insert(source_evidence), batch)

    if technical_source_rows:
        conn.execute(
            delete(pit_occurrences).where(
                pit_occurrences.c.source_row_id.in_(technical_source_rows)
            )
        )
    _upsert_canonical(conn, canonical_rows)

    touched_observation_ids = sorted(set(observation_ids.values()))
    accepted_counts: dict[str, tuple[int, int]] = defaultdict(lambda: (0, 0))
    if observation_keys:
        count_query = (
            select(
                evidence_claims.c.observation_key,
                func.count(evidence_claims.c.evidence_key).label("accepted"),
                func.count(func.distinct(evidence_claims.c.independence_key)).label("independent"),
            )
            .where(evidence_claims.c.observation_key.in_(observation_keys))
            .group_by(evidence_claims.c.observation_key)
        )
        for item in conn.execute(count_query):
            accepted_counts[str(item.observation_key)] = (
                int(item.accepted),
                int(item.independent),
            )
    for key, observation_id in observation_ids.items():
        accepted, independent = accepted_counts[key]
        conn.execute(
            update(pit_observations)
            .where(pit_observations.c.id == observation_id)
            .values(
                occurrence_count=accepted,
                source_count=independent,
                state="new" if key in {claim_by_key[item]["observation_key"] for item in inserted_claims} else pit_observations.c.state,
                last_seen_at=now,
                updated_at=now,
            )
        )

    return {
        "observation_ids": touched_observation_ids,
        "accepted_evidence": len(inserted_claims),
        "independent_evidence": len(inserted_claims),
        "technical_duplicates": len(technical_source_rows),
        "canonical_addresses": len({item["canonical_address_key"] for item in canonical_rows}),
    }
