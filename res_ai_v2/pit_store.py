from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any

from sqlalchemy import bindparam, func, insert, select, update
from sqlalchemy.engine import Connection

from .normalize import normalize_entity, normalize_text, sha256_parts
from .pit_schema import pit_observations, pit_occurrences

BATCH_SIZE = 1000


def _chunks(values: list[Any], size: int = BATCH_SIZE):
    for start in range(0, len(values), size):
        yield values[start : start + size]


def observation_key(row: dict[str, Any]) -> str:
    return sha256_parts(
        [
            normalize_text(str(row.get("branch", ""))),
            normalize_text(str(row.get("res", ""))),
            normalize_entity(str(row.get("locality", ""))),
            normalize_entity(str(row.get("district", ""))),
            normalize_entity(str(row.get("settlement", ""))),
            normalize_entity(str(row.get("street", ""))),
            normalize_text(str(row.get("text", ""))),
        ]
    )


def _payload(row: dict[str, Any], now: datetime) -> dict[str, Any]:
    locality = str(row.get("locality", "")).strip()
    district = str(row.get("district", "")).strip()
    settlement = str(row.get("settlement", "")).strip()
    street = str(row.get("street", "")).strip()
    text = str(row.get("text", "")).strip()
    return {
        "observation_key": observation_key(row),
        "canonical_hash": str(row.get("canonical_hash", "")),
        "branch_name": str(row.get("branch", "")).strip(),
        "res_name": str(row.get("res", "")).strip(),
        "locality": locality,
        "district": district,
        "settlement": settlement,
        "street": street,
        "raw_text": text,
        "locality_key": normalize_entity(locality),
        "district_key": normalize_entity(district),
        "settlement_key": normalize_entity(settlement),
        "street_key": normalize_entity(street),
        "text_key": normalize_text(text),
        "occurrence_count": 1,
        "source_count": 1,
        "state": "new",
        "first_seen_at": now,
        "last_seen_at": now,
        "updated_at": now,
    }


def ingest_pit_rows(
    conn: Connection,
    *,
    source_file_id: int,
    rows: list[dict[str, Any]],
    source_row_ids: dict[tuple[str, int, str], int],
    now: datetime,
) -> dict[str, Any]:
    """Пакетно сохраняет исходные строки в неизменяемую яму."""
    prepared: list[tuple[dict[str, Any], int]] = []
    payload_by_key: dict[str, dict[str, Any]] = {}
    occurrences_in_file: dict[str, int] = defaultdict(int)
    for row in rows:
        canonical_hash = str(row.get("canonical_hash", ""))
        source_row_id = source_row_ids.get(
            (
                canonical_hash,
                int(row.get("row_number", 0)),
                str(row.get("sheet_name", "")),
            )
        )
        if not source_row_id:
            continue
        payload = _payload(row, now)
        key = str(payload["observation_key"])
        payload_by_key.setdefault(key, payload)
        occurrences_in_file[key] += 1
        prepared.append((payload, source_row_id))

    keys = list(payload_by_key)
    observation_ids: dict[str, int] = {}
    for batch in _chunks(keys):
        observation_ids.update(
            {
                str(item.observation_key): int(item.id)
                for item in conn.execute(
                    select(pit_observations.c.id, pit_observations.c.observation_key).where(
                        pit_observations.c.observation_key.in_(batch)
                    )
                )
            }
        )
    existing_ids = set(observation_ids.values())

    missing = [payload for key, payload in payload_by_key.items() if key not in observation_ids]
    for batch in _chunks(missing):
        if batch:
            conn.execute(insert(pit_observations), batch)

    for batch in _chunks(keys):
        observation_ids.update(
            {
                str(item.observation_key): int(item.id)
                for item in conn.execute(
                    select(pit_observations.c.id, pit_observations.c.observation_key).where(
                        pit_observations.c.observation_key.in_(batch)
                    )
                )
            }
        )

    occurrence_payload: list[dict[str, Any]] = []
    touched: set[int] = set()
    for payload, source_row_id in prepared:
        observation_id = observation_ids[str(payload["observation_key"])]
        touched.add(observation_id)
        occurrence_payload.append(
            {
                "observation_id": observation_id,
                "source_file_id": source_file_id,
                "source_row_id": source_row_id,
                "created_at": now,
            }
        )
    for batch in _chunks(occurrence_payload):
        if batch:
            conn.execute(insert(pit_occurrences), batch)

    counts: dict[int, tuple[int, int]] = {}
    for batch in _chunks(sorted(touched)):
        query = (
            select(
                pit_occurrences.c.observation_id,
                func.count(pit_occurrences.c.id).label("occurrences"),
                func.count(func.distinct(pit_occurrences.c.source_file_id)).label("sources"),
            )
            .where(pit_occurrences.c.observation_id.in_(batch))
            .group_by(pit_occurrences.c.observation_id)
        )
        for item in conn.execute(query):
            counts[int(item.observation_id)] = (int(item.occurrences), int(item.sources))

    update_rows = []
    for key, observation_id in observation_ids.items():
        occurrences, sources = counts.get(observation_id, (1, 1))
        if observation_id not in existing_ids and occurrences_in_file.get(key, 1) == 1:
            continue
        update_rows.append(
            {
                "b_id": observation_id,
                "b_occurrences": occurrences,
                "b_sources": sources,
                "b_now": now,
            }
        )
    if update_rows:
        statement = (
            update(pit_observations)
            .where(pit_observations.c.id == bindparam("b_id"))
            .values(
                occurrence_count=bindparam("b_occurrences"),
                source_count=bindparam("b_sources"),
                state="new",
                last_seen_at=bindparam("b_now"),
                updated_at=bindparam("b_now"),
            )
        )
        for batch in _chunks(update_rows):
            conn.execute(statement, batch)

    duplicate_observations = sum(1 for occurrences, _ in counts.values() if occurrences > 1)
    return {
        "observation_ids": sorted(touched),
        "observations": len(touched),
        "occurrences": len(occurrence_payload),
        "duplicate_observations": duplicate_observations,
    }


def load_observations(
    conn: Connection,
    observation_ids: list[int] | None = None,
) -> list[dict[str, Any]]:
    query = select(pit_observations)
    if observation_ids is not None:
        if not observation_ids:
            return []
        query = query.where(pit_observations.c.id.in_(sorted(set(observation_ids))))
    return [dict(row._mapping) for row in conn.execute(query)]


def observation_groups(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        address_key = sha256_parts(
            [
                str(row.get("locality_key", "")),
                str(row.get("district_key", "")),
                str(row.get("settlement_key", "")),
                str(row.get("street_key", "")),
            ]
        )
        result[address_key].append(row)
    return dict(result)
