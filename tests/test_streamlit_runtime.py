from __future__ import annotations


def _reset_runtime(runtime) -> None:
    thread = runtime._RUNTIME_THREAD
    if thread and thread.is_alive():
        thread.join(timeout=2)
    runtime._RUNTIME_THREAD = None
    runtime._RUNTIME_STATE.update({"ready": False, "running": False, "error": ""})


def test_streamlit_runtime_starts_in_background_once():
    from res_ai_v2 import ui_runtime

    _reset_runtime(ui_runtime)
    calls = {"count": 0}

    def initialize() -> None:
        calls["count"] += 1

    ui_runtime.start_runtime_async(initialize)
    assert ui_runtime._RUNTIME_THREAD is not None
    ui_runtime._RUNTIME_THREAD.join(timeout=2)
    assert not ui_runtime._RUNTIME_THREAD.is_alive()
    assert ui_runtime.runtime_status()["ready"] is True

    ui_runtime.start_runtime_async(initialize)
    assert calls == {"count": 1}
    _reset_runtime(ui_runtime)


def test_streamlit_runtime_keeps_error_without_blocking():
    from res_ai_v2 import ui_runtime

    _reset_runtime(ui_runtime)

    def fail() -> None:
        raise RuntimeError("neon unavailable")

    ui_runtime.start_runtime_async(fail)
    assert ui_runtime._RUNTIME_THREAD is not None
    ui_runtime._RUNTIME_THREAD.join(timeout=2)

    status = ui_runtime.runtime_status()
    assert status["ready"] is False
    assert status["running"] is False
    assert "neon unavailable" in status["error"]
    _reset_runtime(ui_runtime)


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


def test_local_pages_render_before_database_is_ready():
    from pathlib import Path

    source = Path("res_ai_v2/ui.py").read_text(encoding="utf-8")
    assert 'if page == "Загрузка"' in source
    assert 'page_upload(database_ready=database_ready)' in source
    assert 'if page == "Определение"' in source
    assert 'page_predict(database_ready=database_ready)' in source
    assert source.index('if page == "Загрузка"') < source.index("if not database_ready")


def test_postgres_ui_timeouts_are_short():
    from pathlib import Path

    source = Path("res_ai_v2/db.py").read_text(encoding="utf-8")
    assert '"connect_timeout": 3' in source
    assert '"pool_timeout": 2' in source
    assert "statement_timeout=5000" in source
