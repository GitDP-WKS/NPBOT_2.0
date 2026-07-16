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


def test_local_pages_render_before_database_gate():
    import inspect

    from res_ai_v2 import ui

    source = inspect.getsource(ui.main)
    assert source.index('if page == "Загрузка"') < source.index("if not database_ready")
    assert source.index('if page == "Определение"') < source.index("if not database_ready")
    assert "page_upload(database_ready=database_ready)" in source
    assert "page_predict(database_ready=database_ready)" in source


def test_streamlit_common_path_has_no_blocking_database_calls():
    import inspect

    from res_ai_v2 import ui

    source = inspect.getsource(ui.main)
    assert "initialize_database()" not in source
    assert "ensure_daily_audit" not in source
    assert "storage_name" not in source


def test_build_id_is_visible():
    from res_ai_v2 import ui

    assert ui.BUILD_ID == "2026.07.16.2"
