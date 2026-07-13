from __future__ import annotations

import hashlib
from typing import Any

from sqlalchemy import MetaData, Table, inspect, select, update

from .db import get_engine, get_setting, initialize_database, set_setting, utcnow
from .import_service import import_plan
from .importer import ImportPlan, SheetPlan
from .normalize import normalize_entity, normalize_text, sha256_parts
from .repositories import audit
from .schema import address_mappings, addresses, query_rules
from .structure import canonical_executor

LEGACY_KEY = "legacy_v1_import_complete"


def legacy_available() -> bool:
    initialize_database(); return inspect(get_engine()).has_table("res_ai_knowledge")


def migrate_legacy(actor: str = "Администратор") -> dict[str, Any]:
    initialize_database()
    if get_setting(LEGACY_KEY, "0") == "1": return {"already_migrated": True, "rows": 0, "human_verified": 0, "query_rules": 0}
    engine = get_engine(); inspector = inspect(engine)
    if not inspector.has_table("res_ai_knowledge"): raise ValueError("Таблица старой версии res_ai_knowledge не найдена.")
    legacy_meta = MetaData(); old_knowledge = Table("res_ai_knowledge", legacy_meta, autoload_with=engine)
    with engine.connect() as conn: old_rows = [dict(row._mapping) for row in conn.execute(select(old_knowledge))]
    prepared = []
    for index, row in enumerate(old_rows, start=1):
        branch, res, known = canonical_executor(row.get("branch"), row.get("res"))
        if not res: continue
        prepared.append({"branch": branch, "res": res, "known_res": known, "locality": str(row.get("locality", "") or ""), "district": str(row.get("district", "") or ""), "settlement": str(row.get("settlement", "") or ""), "street": str(row.get("street", "") or ""), "text": "", "record_number": str(row.get("id", index)), "sheet_name": "legacy_res_ai_knowledge", "row_number": index + 1, "raw": row, "legacy_status": str(row.get("status", "")), "legacy_confirmations": int(row.get("confirmations", 0) or 0)})
    digest = hashlib.sha256("|".join(sorted(sha256_parts([row["branch"], row["res"], row["locality"], row["district"], row["settlement"], row["street"], row["legacy_status"], row["legacy_confirmations"]]) for row in prepared)).encode()).hexdigest()
    plan = ImportPlan(file_hash=f"legacy-{digest}", file_name="Миграция данных РЭС AI 1", source_kind="legacy", sheets=[SheetPlan(sheet_name="legacy_res_ai_knowledge", header_row=0, columns={}, confidence={}, all_columns=[], rows=prepared)], detected_rows=len(prepared), warnings=[])
    result = import_plan(plan, actor=actor); human_verified = 0
    with engine.begin() as conn:
        for row in prepared:
            if row["legacy_confirmations"] <= 0 or row["legacy_status"] == "rejected": continue
            address_key = sha256_parts([normalize_entity(row["locality"]), normalize_entity(row["district"]), normalize_entity(row["settlement"]), normalize_entity(row["street"])])
            address = conn.execute(select(addresses.c.id).where(addresses.c.address_key == address_key)).first()
            if not address: continue
            mapping = conn.execute(select(address_mappings.c.id).where(address_mappings.c.address_id == address.id, address_mappings.c.res_name == row["res"])).first()
            if not mapping: continue
            conn.execute(update(address_mappings).where(address_mappings.c.id == mapping.id).values(status="human_verified", source_confidence=100.0, human_confirmations=max(1, row["legacy_confirmations"]), active=True, updated_at=utcnow())); human_verified += 1
    migrated_rules = 0
    if inspector.has_table("res_ai_query_rules"):
        old_rules = Table("res_ai_query_rules", legacy_meta, autoload_with=engine)
        with engine.begin() as conn:
            for row in conn.execute(select(old_rules)):
                data = dict(row._mapping); normalized = normalize_text(data.get("normalized_query", ""))
                if not normalized or conn.execute(select(query_rules.c.id).where(query_rules.c.normalized_query == normalized)).first(): continue
                conn.execute(query_rules.insert().values(normalized_query=normalized, raw_query=str(data.get("normalized_query", "")), selection_json=str(data.get("selection_json", "[]")), created_by="Миграция старой версии", created_at=utcnow(), updated_at=utcnow())); migrated_rules += 1
    set_setting(LEGACY_KEY, "1"); audit(actor, "legacy_migration", "database", "res_ai_v1", {}, {"rows": len(prepared), "human_verified": human_verified, "query_rules": migrated_rules})
    return {"already_migrated": False, "rows": len(prepared), "human_verified": human_verified, "query_rules": migrated_rules, "import": result}
