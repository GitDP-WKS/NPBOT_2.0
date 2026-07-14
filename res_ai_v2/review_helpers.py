from __future__ import annotations

import json
from typing import Any

from sqlalchemy import insert, select, update

from .db import utcnow
from .normalize import normalize_entity, normalize_text, sha256_parts, stable_json
from .schema import addresses, address_mappings, query_rules, text_examples
from .structure import CURRENT_STRUCTURE


def _address(fields: dict[str, Any]) -> dict[str, Any]:
    values = {
        key: str(fields.get(key, "")).strip()
        for key in ("locality", "district", "settlement", "street")
    }
    keys = {f"{key}_key": normalize_entity(value) for key, value in values.items()}
    return {
        **values,
        **keys,
        "address_key": sha256_parts([keys[f"{key}_key"] for key in values]),
    }


def _upsert_address(conn, fields: dict[str, Any]) -> int:
    values, now = _address(fields), utcnow()
    row = conn.execute(
        select(addresses.c.id).where(addresses.c.address_key == values["address_key"])
    ).first()
    if row:
        conn.execute(
            update(addresses)
            .where(addresses.c.id == row.id)
            .values(
                **{k: v for k, v in values.items() if k != "address_key"},
                updated_at=now,
            )
        )
        return int(row.id)
    return int(
        conn.execute(
            insert(addresses).values(**values, created_at=now, updated_at=now)
        ).inserted_primary_key[0]
    )


def _upsert_mapping(conn, address_id: int, res_name: str, votes: int) -> int:
    row = conn.execute(
        select(address_mappings).where(
            address_mappings.c.address_id == address_id,
            address_mappings.c.res_name == res_name,
        )
    ).first()
    now = utcnow()
    values = dict(
        branch_name=CURRENT_STRUCTURE[res_name],
        status="human_verified",
        source_confidence=100.0,
        human_confirmations=max(1, votes),
        active=True,
        updated_at=now,
    )
    if row:
        conn.execute(
            update(address_mappings).where(address_mappings.c.id == row.id).values(**values)
        )
        return int(row.id)
    return int(
        conn.execute(
            insert(address_mappings).values(
                address_id=address_id,
                res_name=res_name,
                created_at=now,
                **values,
            )
        ).inserted_primary_key[0]
    )


def _save_query(conn, text: str, selected: list[str], actor: str) -> None:
    normalized, now = normalize_text(text), utcnow()
    encoded = stable_json(
        [{"res": res, "branch": CURRENT_STRUCTURE[res]} for res in selected]
    )
    row = conn.execute(
        select(query_rules.c.id).where(query_rules.c.normalized_query == normalized)
    ).first()
    values = dict(
        raw_query=text,
        selection_json=encoded,
        created_by=actor,
        updated_at=now,
    )
    if row:
        conn.execute(update(query_rules).where(query_rules.c.id == row.id).values(**values))
    else:
        conn.execute(
            insert(query_rules).values(
                normalized_query=normalized,
                created_at=now,
                **values,
            )
        )


def _save_text(
    conn,
    text: str,
    address_id: int | None,
    res: str,
    votes: int,
) -> tuple[int, bool, dict[str, Any] | None]:
    normalized = normalize_text(text)
    example_hash = sha256_parts([normalized, res])
    now = utcnow()
    row = conn.execute(
        select(text_examples).where(text_examples.c.example_hash == example_hash)
    ).first()
    values = dict(
        raw_text=text,
        normalized_text=normalized,
        address_id=address_id,
        res_name=res,
        branch_name=CURRENT_STRUCTURE[res],
        status="human_verified",
        human_confirmations=max(1, votes),
        weight=3.0,
        updated_at=now,
    )
    if row:
        previous = dict(row._mapping)
        conn.execute(update(text_examples).where(text_examples.c.id == row.id).values(**values))
        return int(row.id), False, previous
    result = conn.execute(
        insert(text_examples).values(
            example_hash=example_hash,
            source_row_id=None,
            created_at=now,
            **values,
        )
    )
    return int(result.inserted_primary_key[0]), True, None


def _apply(
    conn,
    task: dict[str, Any],
    selection: dict[str, Any],
    actor: str,
    votes: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    payload = json.loads(str(task["payload_json"]) or "{}")
    task_type = str(task["task_type"])
    selected = [res for res in selection.get("selected_res", []) if res in CURRENT_STRUCTURE]
    original = payload.get("address", {})
    fields = {
        key: selection.get(key) or original.get(key, "")
        for key in ("locality", "district", "settlement", "street")
    }
    query_text = str(payload.get("query_text", ""))
    old_address_ids: list[int] = []
    if original.get("address_key"):
        row = conn.execute(
            select(addresses.c.id).where(
                addresses.c.address_key == original["address_key"]
            )
        ).first()
        if row:
            old_address_ids.append(int(row.id))
    if payload.get("mapping_id"):
        row = conn.execute(
            select(address_mappings.c.address_id).where(
                address_mappings.c.id == int(payload["mapping_id"])
            )
        ).first()
        if row:
            old_address_ids.append(int(row.address_id))
    old_address_ids = list(set(old_address_ids))
    before = {
        "mappings": [
            dict(row._mapping)
            for row in conn.execute(
                select(address_mappings).where(
                    address_mappings.c.address_id.in_(old_address_ids)
                )
            )
        ]
        if old_address_ids
        else [],
        "query_rule": None,
        "created_mapping_ids": [],
        "created_text_ids": [],
        "previous_texts": [],
    }
    normalized_query = normalize_text(query_text)
    if normalized_query:
        row = conn.execute(
            select(query_rules).where(query_rules.c.normalized_query == normalized_query)
        ).first()
        before["query_rule"] = dict(row._mapping) if row else None

    if task_type in {"mapping_conflict", "duplicate_observation"}:
        if old_address_ids:
            conn.execute(
                update(address_mappings)
                .where(address_mappings.c.address_id.in_(old_address_ids))
                .values(
                    status="rejected",
                    active=False,
                    source_confidence=0.0,
                    human_confirmations=1,
                    updated_at=utcnow(),
                )
            )
            for res in selected:
                before["created_mapping_ids"].append(
                    _upsert_mapping(conn, old_address_ids[0], res, 1)
                )
    elif task_type in {"missing_context", "import_issue"}:
        if not (fields["locality"] or fields["settlement"]):
            raise ValueError("Укажите населенный пункт или СНТ/поселок.")
        if not selected:
            raise ValueError("Выберите правильный РЭС.")
        address_id = _upsert_address(conn, fields)
        if old_address_ids and address_id not in old_address_ids:
            conn.execute(
                update(address_mappings)
                .where(address_mappings.c.address_id.in_(old_address_ids))
                .values(status="rejected", active=False, updated_at=utcnow())
            )
        for res in selected:
            before["created_mapping_ids"].append(
                _upsert_mapping(conn, address_id, res, 1)
            )
    elif task_type in {
        "prediction_review",
        "model_error",
        "low_confidence",
        "unknown_address",
    }:
        address_id = (
            _upsert_address(conn, fields)
            if fields["locality"] or fields["settlement"]
            else None
        )
        if address_id:
            for res in selected:
                before["created_mapping_ids"].append(
                    _upsert_mapping(conn, address_id, res, 1)
                )
        if query_text:
            _save_query(conn, query_text, selected, actor)
            for res in selected:
                item_id, created, previous = _save_text(
                    conn,
                    query_text,
                    address_id,
                    res,
                    1,
                )
                if created:
                    before["created_text_ids"].append(item_id)
                elif previous:
                    before["previous_texts"].append(previous)
    else:
        raise ValueError(f"Неизвестный тип задания: {task_type}")
    return before, {
        "selected_res": selected,
        "address": fields,
        "query_text": query_text,
        "task_type": task_type,
    }
