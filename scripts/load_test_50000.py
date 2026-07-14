from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("DATABASE_URL", "sqlite:///data/load_test_50000.db")
os.environ.setdefault("RES_AI_ALLOW_SQLITE", "1")
os.environ.setdefault("RES_AI_DISABLE_BACKGROUND_WORKER", "1")

from res_ai_v2.agent import run_agent_cycle  # noqa: E402
from res_ai_v2.db import get_engine, initialize_database  # noqa: E402
from res_ai_v2.import_service import import_plan  # noqa: E402
from res_ai_v2.importer import ImportPlan, SheetPlan  # noqa: E402
from res_ai_v2.repositories import stats  # noqa: E402


def make_plan(size: int = 50_000) -> ImportPlan:
    rows = [
        {
            "branch": "Приволжские сети",
            "res": "Лаишевский район электрических сетей",
            "known_res": True,
            "locality": "Нагрузочный населенный пункт",
            "district": "Лаишевский",
            "settlement": "",
            "street": f"Улица {index}",
            "text": f"Лаишевский район, нагрузочный населенный пункт, улица {index}",
            "record_number": str(index),
            "sheet_name": "Реестр",
            "row_number": index + 2,
            "raw": {"row": index},
        }
        for index in range(size)
    ]
    return ImportPlan(
        file_hash="f" * 64,
        file_name="load_test_50000.xlsx",
        source_kind="mixed",
        sheets=[SheetPlan("Реестр", 0, {}, {}, [], rows, [])],
        detected_rows=size,
        warnings=[],
    )


def main() -> None:
    database = ROOT / "data" / "load_test_50000.db"
    database.parent.mkdir(parents=True, exist_ok=True)
    if database.exists():
        database.unlink()
    os.environ["DATABASE_URL"] = f"sqlite:///{database}"
    get_engine.cache_clear()
    initialize_database.cache_clear()
    initialize_database()

    started = time.perf_counter()
    imported = import_plan(make_plan(), wait_for_agent=False)
    import_seconds = time.perf_counter() - started

    started = time.perf_counter()
    cycle = run_agent_cycle(max_events=100, worker_id="load-test")
    analysis_seconds = time.perf_counter() - started

    values = stats()
    print(
        {
            "import_seconds": round(import_seconds, 2),
            "analysis_seconds": round(analysis_seconds, 2),
            "imported": imported["imported"],
            "agent_failed": cycle.failed,
            "addresses": values["addresses"],
            "mappings": values["mappings"],
        }
    )
    if imported["imported"] != 50_000 or cycle.failed or values["mappings"] != 50_000:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
