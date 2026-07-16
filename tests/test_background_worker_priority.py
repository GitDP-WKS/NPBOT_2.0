from types import SimpleNamespace


def test_daily_audit_is_not_called_on_every_worker_cycle(monkeypatch):
    from res_ai_v2 import background_worker

    calls = {"daily": 0, "cycles": []}

    def daily() -> None:
        calls["daily"] += 1

    def cycle(*, max_events: int, worker_id: str):
        calls["cycles"].append((max_events, worker_id))
        return SimpleNamespace(processed=0, completed=0, failed=0)

    monkeypatch.setattr(background_worker, "ensure_daily_audit", daily)
    monkeypatch.setattr(background_worker, "run_agent_cycle", cycle)
    monkeypatch.setitem(background_worker._STATE, "last_daily_check", 0.0)

    background_worker.run_worker_iteration(now=3600.0)
    background_worker.run_worker_iteration(now=3601.0)

    assert calls["daily"] == 1
    assert calls["cycles"] == [
        (5, "background-worker"),
        (5, "background-worker"),
    ]


def test_worker_idle_interval_leaves_time_for_streamlit():
    from res_ai_v2 import background_worker

    assert background_worker._IDLE_SLEEP >= 5.0
    assert background_worker._BUSY_SLEEP > 0
    assert background_worker._DAILY_CHECK_INTERVAL >= 3600.0
