from __future__ import annotations

from typing import Any

from sqlalchemy import insert, select, update

from .agent import run_agent_cycle
from .db import bump_data_version, get_engine, initialize_database, utcnow
from .event_bus import publish_event
from .importer import ImportPlan, canonical_row_key
from .normalize import normalize_entity, normalize_text, row_hash, sha256_parts, stable_json
from .repositories import audit, create_or_update_task
from .schema import address_mappings, addresses, mapping_evidence, source_files, source_rows, text_examples
from .structure import CURRENT_STRUCTURE, canonical_executor

BATCH = 500


def chunks(values: list[Any], size: int = BATCH):
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _existing(conn, column, values: list[Any]) -> set[Any]:
    result: set[Any] = set()
    for batch in chunks(values, 1000):
        result.update(conn.scalars(select(column).where(column.in_(batch))))
    return result


def import_plan(plan: ImportPlan, actor: str = "Администратор") -> dict[str, Any]:
    initialize_database()
    engine, now = get_engine(), utcnow()
    with engine.connect() as conn:
        if conn.execute(select(source_files.c.id).where(source_files.c.file_hash == plan.file_hash)).first():
            return {"already_loaded": True, "seen": plan.detected_rows, "imported": 0, "duplicates": plan.detected_rows, "issues": 0, "analysis": None}

    rows: list[dict[str, Any]] = []
    for sheet in plan.sheets:
        for source in sheet.rows:
            row = dict(source)
            row["branch"], row["res"], row["known_res"] = canonical_executor(row.get("branch"), row.get("res"))
            row["sheet_name"] = sheet.sheet_name
            rows.append(row)
    unique: dict[str, dict[str, Any]] = {}
    for row in rows:
        unique.setdefault(canonical_row_key(row), row)
    issues: list[dict[str, Any]] = []

    with engine.begin() as conn:
        source_id = int(conn.execute(insert(source_files).values(file_hash=plan.file_hash, file_name=plan.file_name, source_kind=plan.source_kind, row_count=len(rows), imported_rows=0, status="importing", imported_at=now)).inserted_primary_key[0])
        raw_payload = []
        for row in rows:
            raw = row.get("raw", {})
            raw_payload.append({"source_file_id": source_id, "sheet_name": str(row.get("sheet_name", "")), "row_number": int(row.get("row_number", 0)), "raw_json": stable_json(raw), "raw_hash": row_hash(raw), "canonical_hash": canonical_row_key(row), "created_at": now})
        for batch in chunks(raw_payload):
            conn.execute(insert(source_rows), batch)
        source_ids = {(str(item.canonical_hash), int(item.row_number), str(item.sheet_name)): int(item.id) for item in conn.execute(select(source_rows.c.id, source_rows.c.canonical_hash, source_rows.c.row_number, source_rows.c.sheet_name).where(source_rows.c.source_file_id == source_id))}

        address_payload: dict[str, dict[str, Any]] = {}
        canonical_to_address: dict[str, str] = {}
        for canonical_hash, row in unique.items():
            locality, district = str(row.get("locality", "")), str(row.get("district", ""))
            settlement, street = str(row.get("settlement", "")), str(row.get("street", ""))
            if not (locality or settlement):
                continue
            keys = [normalize_entity(value) for value in (locality, district, settlement, street)]
            address_key = sha256_parts(keys)
            canonical_to_address[canonical_hash] = address_key
            address_payload[address_key] = {"address_key": address_key, "locality": locality, "district": district, "settlement": settlement, "street": street, "locality_key": keys[0], "district_key": keys[1], "settlement_key": keys[2], "street_key": keys[3], "created_at": now, "updated_at": now}
        address_keys = list(address_payload)
        existing_address_keys = _existing(conn, addresses.c.address_key, address_keys)
        for batch in chunks([item for key, item in address_payload.items() if key not in existing_address_keys]):
            conn.execute(insert(addresses), batch)
        address_ids: dict[str, int] = {}
        for batch in chunks(address_keys, 1000):
            address_ids.update({str(item.address_key): int(item.id) for item in conn.execute(select(addresses.c.id, addresses.c.address_key).where(addresses.c.address_key.in_(batch)))})

        wanted: dict[tuple[int, str], dict[str, Any]] = {}
        for canonical_hash, row in unique.items():
            address_id = address_ids.get(canonical_to_address.get(canonical_hash, ""))
            branch, res = str(row.get("branch", "")), str(row.get("res", ""))
            if address_id and res in CURRENT_STRUCTURE and branch:
                wanted[(address_id, res)] = {"address_id": address_id, "res_name": res, "branch_name": branch, "status": "source_only", "source_confidence": 80.0, "human_confirmations": 0, "active": True, "created_at": now, "updated_at": now}
            elif res or branch or row.get("text"):
                task_key = sha256_parts(["import_issue", plan.file_hash, canonical_hash])
                issues.append({"task_key": task_key, "task_type": "import_issue", "subject_type": "source_row", "subject_key": canonical_hash, "title": "Строка импорта требует уточнения", "payload": {"raw": row.get("raw", {}), "detected": {key: row.get(key, "") for key in ("branch", "res", "locality", "district", "settlement", "street", "text")}, "options": [], "allow_multiple": False, "allow_address_edit": True}, "priority": 90})
        wanted_address_ids = list({key[0] for key in wanted})
        mapping_ids: dict[tuple[int, str], int] = {}
        for batch in chunks(wanted_address_ids, 1000):
            mapping_ids.update({(int(item.address_id), str(item.res_name)): int(item.id) for item in conn.execute(select(address_mappings.c.id, address_mappings.c.address_id, address_mappings.c.res_name).where(address_mappings.c.address_id.in_(batch)))})
        for batch in chunks([item for key, item in wanted.items() if key not in mapping_ids]):
            conn.execute(insert(address_mappings), batch)
        for batch in chunks(wanted_address_ids, 1000):
            mapping_ids.update({(int(item.address_id), str(item.res_name)): int(item.id) for item in conn.execute(select(address_mappings.c.id, address_mappings.c.address_id, address_mappings.c.res_name).where(address_mappings.c.address_id.in_(batch)))})

        evidence_payload, text_payload = [], []
        for canonical_hash, row in unique.items():
            address_id = address_ids.get(canonical_to_address.get(canonical_hash, ""))
            mapping_id = mapping_ids.get((address_id or 0, str(row.get("res", ""))))
            source_row_id = source_ids.get((canonical_hash, int(row.get("row_number", 0)), str(row.get("sheet_name", ""))))
            if mapping_id:
                evidence_payload.append({"mapping_id": mapping_id, "source_row_id": source_row_id, "evidence_type": "source_row", "evidence_key": canonical_hash, "weight": 1.0, "created_at": now})
            text = str(row.get("text", "")).strip()
            res = str(row.get("res", ""))
            if text and address_id and res in CURRENT_STRUCTURE:
                text_payload.append({"example_hash": sha256_parts([normalize_text(text), res]), "source_row_id": source_row_id, "raw_text": text, "normalized_text": normalize_text(text), "address_id": address_id, "res_name": res, "branch_name": str(row.get("branch", "")), "status": "source_only", "human_confirmations": 0, "weight": 1.0, "created_at": now, "updated_at": now})

        if evidence_payload:
            mapping_set = list({item["mapping_id"] for item in evidence_payload})
            old_evidence = {(int(item.mapping_id), str(item.evidence_key)) for item in conn.execute(select(mapping_evidence.c.mapping_id, mapping_evidence.c.evidence_key).where(mapping_evidence.c.mapping_id.in_(mapping_set)))}
            for batch in chunks([item for item in evidence_payload if (int(item["mapping_id"]), str(item["evidence_key"])) not in old_evidence]):
                conn.execute(insert(mapping_evidence), batch)
        old_examples = _existing(conn, text_examples.c.example_hash, [item["example_hash"] for item in text_payload])
        fresh_text = [item for item in {item["example_hash"]: item for item in text_payload}.values() if item["example_hash"] not in old_examples]
        for batch in chunks(fresh_text):
            conn.execute(insert(text_examples), batch)
        conn.execute(update(source_files).where(source_files.c.id == source_id).values(imported_rows=len(unique), status="imported"))

    for task in issues:
        create_or_update_task(**task)
    bump_data_version()
    event_id = publish_event(
        "file_imported",
        "source_file",
        str(source_id),
        {"file_hash": plan.file_hash, "address_ids": sorted(set(address_ids.values()))},
        deduplication_key=plan.file_hash,
    )
    cycle = run_agent_cycle(max_events=20)
    event_result = next((item for item in cycle.results if item.get("event_id") == event_id), None)
    analysis = ((event_result or {}).get("result") or {}).get("analysis")
    audit(actor, "import_file", "source_file", plan.file_hash, {}, {"file_name": plan.file_name, "rows": len(rows), "unique": len(unique), "issues": len(issues), "event_id": event_id})
    return {"already_loaded": False, "seen": len(rows), "imported": len(unique), "duplicates": len(rows) - len(unique), "issues": len(issues), "text_examples": len(fresh_text), "analysis": analysis, "agent": {"processed": cycle.processed, "completed": cycle.completed, "failed": cycle.failed}}
