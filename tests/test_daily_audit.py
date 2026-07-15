from __future__ import annotations

from datetime import UTC, datetime

from res_ai_v2.agent import run_until_event
from res_ai_v2.daily_audit import ensure_daily_audit, latest_daily_audit


def test_daily_audit_runs_only_once_per_day(temp_db) -> None:
    morning = datetime(2026, 7, 14, 5, 0, tzinfo=UTC)
    event_id = ensure_daily_audit(morning)
    assert event_id is not None

    result = run_until_event(event_id, max_events=50, worker_id="daily-test")
    assert result["target_status"] == "completed"
    assert ensure_daily_audit(datetime(2026, 7, 14, 20, 0, tzinfo=UTC)) is None

    latest = latest_daily_audit()
    assert latest is not None
    assert latest["run_date"] == "2026-07-14"
    assert latest["status"] == "completed"


def test_next_day_creates_new_audit(temp_db) -> None:
    first = ensure_daily_audit(datetime(2026, 7, 14, 5, 0, tzinfo=UTC))
    assert first is not None
    run_until_event(first, max_events=50, worker_id="daily-test")

    second = ensure_daily_audit(datetime(2026, 7, 15, 5, 0, tzinfo=UTC))
    assert second is not None
    assert second != first
