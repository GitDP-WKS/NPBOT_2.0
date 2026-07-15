from __future__ import annotations


def _reset_runtime(ui) -> None:
    thread = ui._RUNTIME_THREAD
    if thread and thread.is_alive():
        thread.join(timeout=2)
    ui._RUNTIME_THREAD = None
    ui._RUNTIME_STATE.update({"ready": False, "running": False, "error": ""})


def test_streamlit_runtime_starts_in_background_once(monkeypatch):
    from res_ai_v2 import ui

    _reset_runtime(ui)
    calls = {"database": 0, "worker": 0}

    def initialize_database() -> None:
        calls["database"] += 1

    def start_background_worker() -> bool:
        calls["worker"] += 1
        return True

    monkeypatch.setattr(ui, "initialize_database", initialize_database)
    monkeypatch.setattr(ui, "start_background_worker", start_background_worker)

    ui._start_runtime_async()
    assert ui._RUNTIME_THREAD is not None
    ui._RUNTIME_THREAD.join(timeout=2)
    assert not ui._RUNTIME_THREAD.is_alive()
    assert ui.runtime_status()["ready"] is True

    ui._start_runtime_async()
    assert calls == {"database": 1, "worker": 1}
    _reset_runtime(ui)


def test_streamlit_runtime_keeps_error_without_blocking(monkeypatch):
    from res_ai_v2 import ui

    _reset_runtime(ui)

    def fail() -> None:
        raise RuntimeError("neon unavailable")

    monkeypatch.setattr(ui, "initialize_database", fail)
    ui._start_runtime_async()
    assert ui._RUNTIME_THREAD is not None
    ui._RUNTIME_THREAD.join(timeout=2)

    status = ui.runtime_status()
    assert status["ready"] is False
    assert status["running"] is False
    assert "neon unavailable" in status["error"]
    _reset_runtime(ui)


def test_storage_label_does_not_initialize_database(monkeypatch):
    from res_ai_v2 import db

    def forbidden() -> None:
        raise AssertionError("storage_name must not initialize the database")

    monkeypatch.setattr(db, "initialize_database", forbidden)
    assert db.storage_name() in {"PostgreSQL / Neon", "SQLite (локально)"}


def test_streamlit_render_does_not_schedule_daily_audit():
    from pathlib import Path

    source = Path("res_ai_v2/ui.py").read_text(encoding="utf-8")

    assert "ensure_daily_audit" not in source
