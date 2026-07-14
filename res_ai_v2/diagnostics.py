from __future__ import annotations

from typing import Any

from sqlalchemy import func, select

from .agent_monitor import agent_status
from .db import get_engine, initialize_database
from .schema import address_mappings, addresses, review_tasks, source_files, text_examples
from .structure import CURRENT_STRUCTURE


def run_diagnostics() -> dict[str, Any]:
    initialize_database()
    with get_engine().connect() as conn:
        orphan_mappings = int(
            conn.scalar(
                select(func.count())
                .select_from(
                    address_mappings.outerjoin(addresses, address_mappings.c.address_id == addresses.c.id)
                )
                .where(addresses.c.id.is_(None))
            )
            or 0
        )
        unofficial_res = int(
            conn.scalar(
                select(func.count())
                .select_from(address_mappings)
                .where(address_mappings.c.res_name.not_in(list(CURRENT_STRUCTURE)))
            )
            or 0
        )
        empty_addresses = int(
            conn.scalar(
                select(func.count())
                .select_from(addresses)
                .where(addresses.c.locality_key == "", addresses.c.settlement_key == "")
            )
            or 0
        )
        failed_imports = int(
            conn.scalar(
                select(func.count())
                .select_from(source_files)
                .where(source_files.c.status.not_in(["imported", "completed"]))
            )
            or 0
        )
        open_tasks = int(
            conn.scalar(
                select(func.count()).select_from(review_tasks).where(review_tasks.c.status == "open")
            )
            or 0
        )
        text_count = int(conn.scalar(select(func.count()).select_from(text_examples)) or 0)
        mapping_count = int(
            conn.scalar(
                select(func.count()).select_from(address_mappings).where(address_mappings.c.active.is_(True))
            )
            or 0
        )

    agent = agent_status()
    problems: list[dict[str, Any]] = []
    if orphan_mappings:
        problems.append({"code": "orphan_mappings", "title": "Найдены связи без адреса", "count": orphan_mappings})
    if unofficial_res:
        problems.append({"code": "unofficial_res", "title": "Найдены неофициальные РЭС", "count": unofficial_res})
    if empty_addresses:
        problems.append({"code": "empty_addresses", "title": "Найдены пустые адреса", "count": empty_addresses})
    if failed_imports:
        problems.append({"code": "failed_imports", "title": "Есть незавершенные загрузки", "count": failed_imports})
    if agent["counts"].get("failed", 0):
        problems.append({"code": "failed_events", "title": "Есть события с ошибкой", "count": agent["counts"]["failed"]})

    return {
        "healthy": not problems,
        "problems": problems,
        "counts": {
            "active_mappings": mapping_count,
            "text_examples": text_count,
            "open_tasks": open_tasks,
            "orphan_mappings": orphan_mappings,
            "unofficial_res": unofficial_res,
            "empty_addresses": empty_addresses,
            "failed_imports": failed_imports,
        },
        "agent": agent,
    }
