from __future__ import annotations

from typing import Any

from sqlalchemy import func, insert, select, update
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from .normalize import normalize_entity, normalize_text, sha256_parts
from .pit_schema import knowledge_directives, pit_observations, pit_occurrences
from .schema import (
    address_mappings,
    addresses,
    mapping_evidence,
    review_decisions,
    review_tasks,
    source_rows,
    text_examples,
)

BOOTSTRAP_SETTING = "pit_bootstrap_v1"


def _observation_payload(row: dict[str, Any], now, raw_text: str = "") -> dict[str, Any]:
    locality = str(row.get("locality", "") or "").strip()
    district = str(row.get("district", "") or "").strip()
    settlement = str(row.get("settlement", "") or "").strip()
    street = str(row.get("street", "") or "").strip()
    text = str(raw_text or "").strip()
    locality_key = normalize_entity(locality)
    district_key = normalize_entity(district)
    settlement_key = normalize_entity(settlement)
    street_key = normalize_entity(street)
    text_key = normalize_text(text)
    observation_key = sha256_parts(
        [
            normalize_text(str(row.get("branch_name", ""))),
            normalize_text(str(row.get("res_name", ""))),
            locality_key,
            district_key,
            settlement_key,
            street_key,
            text_key,
        ]
    )
    return {
        "observation_key": observation_key,
        "canonical_hash": str(row.get("address_key", "")) or observation_key,
        "branch_name": str(row.get("branch_name", "")),
        "res_name": str(row.get("res_name", "")),
        "locality": locality,
        "district": district,
        "settlement": settlement,
        "street": street,
        "raw_text": text,
        "locality_key": locality_key,
        "district_key": district_key,
        "settlement_key": settlement_key,
        "street_key": street_key,
        "text_key": text_key,
        "occurrence_count": 1,
        "source_count": 1,
        "state": "analyzed",
        "first_seen_at": now,
        "last_seen_at": now,
        "updated_at": now,
    }


def _insert_observation(conn: Connection, payload: dict[str, Any]) -> int:
    existing = conn.execute(
        select(pit_observations.c.id).where(
            pit_observations.c.observation_key == payload["observation_key"]
        )
    ).first()
    if existing:
        return int(existing.id)
    try:
        return int(
            conn.execute(insert(pit_observations).values(**payload)).inserted_primary_key[0]
        )
    except IntegrityError:
        existing = conn.execute(
            select(pit_observations.c.id).where(
                pit_observations.c.observation_key == payload["observation_key"]
            )
        ).first()
        if not existing:
            raise
        return int(existing.id)


def _link_occurrence(
    conn: Connection,
    observation_id: int,
    source_row_id: int | None,
    now,
) -> None:
    if not source_row_id:
        return
    source = conn.execute(
        select(source_rows.c.source_file_id).where(source_rows.c.id == source_row_id)
    ).first()
    if not source:
        return
    if conn.execute(
        select(pit_occurrences.c.id).where(
            pit_occurrences.c.source_row_id == source_row_id
        )
    ).first():
        return
    conn.execute(
        insert(pit_occurrences).values(
            observation_id=observation_id,
            source_file_id=int(source.source_file_id),
            source_row_id=source_row_id,
            created_at=now,
        )
    )


def _recount(conn: Connection, observation_ids: set[int], now) -> None:
    if not observation_ids:
        return
    rows = conn.execute(
        select(
            pit_occurrences.c.observation_id,
            func.count(pit_occurrences.c.id).label("occurrences"),
            func.count(func.distinct(pit_occurrences.c.source_file_id)).label("sources"),
        )
        .where(pit_occurrences.c.observation_id.in_(sorted(observation_ids)))
        .group_by(pit_occurrences.c.observation_id)
    )
    counts = {
        int(row.observation_id): (int(row.occurrences), int(row.sources))
        for row in rows
    }
    for observation_id in observation_ids:
        occurrences, sources = counts.get(observation_id, (1, 1))
        conn.execute(
            update(pit_observations)
            .where(pit_observations.c.id == observation_id)
            .values(
                occurrence_count=max(1, occurrences),
                source_count=max(1, sources),
                updated_at=now,
            )
        )


def bootstrap_current_knowledge(conn: Connection, now) -> dict[str, int]:
    """Однократно переносит действующую базу в яму без удаления исходных таблиц."""
    if int(conn.scalar(select(func.count()).select_from(pit_observations)) or 0) > 0:
        return {"observations": 0, "occurrences": 0, "directives": 0}

    mapping_rows = [
        dict(row._mapping)
        for row in conn.execute(
            select(
                address_mappings.c.id.label("mapping_id"),
                address_mappings.c.branch_name,
                address_mappings.c.res_name,
                address_mappings.c.status,
                addresses.c.address_key,
                addresses.c.locality,
                addresses.c.district,
                addresses.c.settlement,
                addresses.c.street,
            )
            .select_from(
                address_mappings.join(
                    addresses,
                    address_mappings.c.address_id == addresses.c.id,
                )
            )
            .where(address_mappings.c.active.is_(True))
        )
    ]
    observation_by_mapping: dict[int, int] = {}
    touched: set[int] = set()
    for row in mapping_rows:
        observation_id = _insert_observation(conn, _observation_payload(row, now))
        observation_by_mapping[int(row["mapping_id"])] = observation_id
        touched.add(observation_id)

    occurrence_count_before = int(
        conn.scalar(select(func.count()).select_from(pit_occurrences)) or 0
    )
    if observation_by_mapping:
        for evidence in conn.execute(
            select(mapping_evidence.c.mapping_id, mapping_evidence.c.source_row_id).where(
                mapping_evidence.c.mapping_id.in_(list(observation_by_mapping))
            )
        ):
            _link_occurrence(
                conn,
                observation_by_mapping[int(evidence.mapping_id)],
                int(evidence.source_row_id) if evidence.source_row_id else None,
                now,
            )

    address_data = {
        int(row.id): dict(row._mapping)
        for row in conn.execute(select(addresses))
    }
    for example in conn.execute(select(text_examples)):
        item = dict(example._mapping)
        address = address_data.get(int(item["address_id"])) if item.get("address_id") else {}
        payload = _observation_payload(
            {
                **(address or {}),
                "branch_name": item.get("branch_name", ""),
                "res_name": item.get("res_name", ""),
            },
            now,
            str(item.get("raw_text", "")),
        )
        observation_id = _insert_observation(conn, payload)
        touched.add(observation_id)
        _link_occurrence(
            conn,
            observation_id,
            int(item["source_row_id"]) if item.get("source_row_id") else None,
            now,
        )

    directives = 0
    for decision in conn.execute(
        select(
            review_decisions.c.id,
            review_decisions.c.task_id,
            review_decisions.c.selection_json,
            review_decisions.c.applied_by,
            review_decisions.c.created_at,
            review_tasks.c.subject_type,
            review_tasks.c.subject_key,
        )
        .select_from(
            review_decisions.join(
                review_tasks,
                review_decisions.c.task_id == review_tasks.c.id,
            )
        )
        .where(review_decisions.c.active.is_(True))
    ):
        directive_key = sha256_parts(["legacy_decision", str(decision.id)])
        if conn.execute(
            select(knowledge_directives.c.id).where(
                knowledge_directives.c.directive_key == directive_key
            )
        ).first():
            continue
        conn.execute(
            insert(knowledge_directives).values(
                directive_key=directive_key,
                task_id=int(decision.task_id),
                subject_type=str(decision.subject_type),
                subject_key=str(decision.subject_key),
                selection_json=str(decision.selection_json),
                actor=str(decision.applied_by),
                source_version=0,
                active=True,
                created_at=decision.created_at or now,
                revoked_at=None,
            )
        )
        directives += 1

    _recount(conn, touched, now)
    occurrence_count_after = int(
        conn.scalar(select(func.count()).select_from(pit_occurrences)) or 0
    )
    return {
        "observations": len(touched),
        "occurrences": occurrence_count_after - occurrence_count_before,
        "directives": directives,
    }
