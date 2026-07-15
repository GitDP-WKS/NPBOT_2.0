from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from res_ai_v2.agent import run_until_event  # noqa: E402
from res_ai_v2.daily_audit import ensure_daily_audit, latest_daily_audit  # noqa: E402
from res_ai_v2.db import initialize_database  # noqa: E402


def main() -> None:
    if not os.getenv("DATABASE_URL", "").strip():
        print("DATABASE_URL не задан. Ежедневный самоанализ пропущен.")
        return
    initialize_database()
    event_id = ensure_daily_audit()
    if event_id is None:
        latest = latest_daily_audit()
        print({"status": "already_completed", "latest": latest})
        return
    result = run_until_event(
        event_id,
        max_events=5_000,
        worker_id="github-daily-agent",
    )
    print(result)
    if result["target_status"] != "completed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
