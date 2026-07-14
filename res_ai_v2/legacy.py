from __future__ import annotations

import hashlib
from typing import Any

from sqlalchemy import MetaData, Table, inspect, select

from .db import get_engine, get_setting, initialize_database, set_setting
from .import_service import import_plan
from .importer import ImportPlan, SheetPlan
from .normalize import sha256_parts
from .structure import canonical_executor

LEGACY_KEY = "legacy_v1_import_complete"


def legacy_available() -> bool:
    initialize_database()
    return inspect(get_engine()).has_table("res_ai_knowledge")


def _legacy_rows() -> list[dict[str, Any]]:
    engine = get_engine()
    metadata = MetaData()
    old_knowledge = Table("res_ai_knowledge", metadata, autoload_with=engine)
    with engine.connect() as conn:
        return [dict(row._mapping) for row in conn.execute(select(old_knowledge))]


def migrate_legacy(actor: str = "Администратор") -> dict[str, Any]:
    """Переносит старую базу только в неизменяемую яму, затем запускает агента."""
    initialize_database()
    if get_setting(LEGACY_KEY, "0") == "1":
        return {"already_migrated": True, "rows": 0, "import": None}
    if not legacy_available():
        raise ValueError("Таблица старой версии res_ai_knowledge не найдена.")

    prepared: list[dict[str, Any]] = []
    for index, row in enumerate(_legacy_rows(), start=1):
        branch, res_name, known = canonical_executor(row.get("branch"), row.get("res"))
        if not res_name:
            continue
        confirmations = int(row.get("confirmations", 0) or 0)
        status = str(row.get("status", "") or "")
        source_quality = 0.85 if confirmations > 0 and status != "rejected" else 0.55
        prepared.append(
            {
                "branch": branch,
                "res": res_name,
                "known_res": known,
                "locality": str(row.get("locality", "") or ""),
                "district": str(row.get("district", "") or ""),
                "settlement": str(row.get("settlement", "") or ""),
                "street": str(row.get("street", "") or ""),
                "text": str(row.get("text", "") or ""),
                "record_number": str(row.get("id", index)),
                "sheet_name": "legacy_res_ai_knowledge",
                "row_number": index + 1,
                "source_system": "legacy_res_ai_knowledge",
                "source_event_id": f"legacy:{row.get('id', index)}",
                "source_quality": source_quality,
                "source_accuracy": source_quality,
                "raw": {
                    **row,
                    "source_system": "legacy_res_ai_knowledge",
                    "source_event_id": f"legacy:{row.get('id', index)}",
                    "source_quality": source_quality,
                },
            }
        )

    digest = hashlib.sha256(
        "|".join(
            sorted(
                sha256_parts(
                    [
                        item["res"],
                        item["locality"],
                        item["district"],
                        item["settlement"],
                        item["street"],
                        item["source_event_id"],
                    ]
                )
                for item in prepared
            )
        ).encode()
    ).hexdigest()
    plan = ImportPlan(
        file_hash=f"legacy-{digest}",
        file_name="Миграция данных РЭС AI 1",
        source_kind="legacy",
        sheets=[
            SheetPlan(
                sheet_name="legacy_res_ai_knowledge",
                header_row=0,
                columns={},
                confidence={},
                all_columns=[],
                rows=prepared,
                warnings=[],
            )
        ],
        detected_rows=len(prepared),
        warnings=[],
    )
    result = import_plan(plan, actor=actor, wait_for_agent=True)
    set_setting(LEGACY_KEY, "1")
    return {
        "already_migrated": False,
        "rows": len(prepared),
        "import": result,
    }
