from __future__ import annotations


def test_streamlit_runtime_initializes_once(monkeypatch):
    from res_ai_v2 import ui

    calls = {"database": 0, "worker": 0}

    def initialize_database() -> None:
        calls["database"] += 1

    def start_background_worker() -> bool:
        calls["worker"] += 1
        return True

    monkeypatch.setattr(ui, "initialize_database", initialize_database)
    monkeypatch.setattr(ui, "start_background_worker", start_background_worker)

    ui._initialize_runtime.clear()
    try:
        assert ui._initialize_runtime() is True
        assert ui._initialize_runtime() is True
    finally:
        ui._initialize_runtime.clear()

    assert calls == {"database": 1, "worker": 1}


def test_streamlit_render_does_not_schedule_daily_audit():
    from pathlib import Path

    source = Path("res_ai_v2/ui.py").read_text(encoding="utf-8")

    assert "ensure_daily_audit" not in source
