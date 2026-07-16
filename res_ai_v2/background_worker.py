from __future__ import annotations

import os
import threading
import time
from typing import Any

from . import restore_agent  # noqa: F401
from .agent import run_agent_cycle
from .daily_audit import ensure_daily_audit

_LOCK = threading.Lock()
_THREAD: threading.Thread | None = None
_DAILY_CHECK_INTERVAL = 3600.0
_IDLE_SLEEP = 10.0
_BUSY_SLEEP = 0.25
_STATE: dict[str, Any] = {
    "running": False,
    "last_error": "",
    "processed": 0,
    "completed": 0,
    "failed": 0,
    "last_daily_check": 0.0,
}


def run_worker_iteration(now: float | None = None):
    """Выполняет короткий цикл агента, оставляя приоритет интерфейсу."""
    moment = time.monotonic() if now is None else float(now)
    if moment - float(_STATE["last_daily_check"]) >= _DAILY_CHECK_INTERVAL:
        ensure_daily_audit()
        _STATE["last_daily_check"] = moment

    result = run_agent_cycle(max_events=5, worker_id="background-worker")
    _STATE["processed"] += result.processed
    _STATE["completed"] += result.completed
    _STATE["failed"] += result.failed
    _STATE["last_error"] = ""
    return result


def _loop() -> None:
    _STATE["running"] = True
    time.sleep(1.0)
    while True:
        try:
            result = run_worker_iteration()
            time.sleep(_BUSY_SLEEP if result.processed else _IDLE_SLEEP)
        except Exception as exc:
            _STATE["last_error"] = str(exc)[:1000]
            time.sleep(_IDLE_SLEEP)


def start_background_worker() -> bool:
    """Запускает один фоновый поток на процесс приложения."""
    global _THREAD
    if os.getenv("RES_AI_DISABLE_BACKGROUND_WORKER", "").strip() == "1":
        return False
    with _LOCK:
        if _THREAD and _THREAD.is_alive():
            return False
        _THREAD = threading.Thread(
            target=_loop,
            name="res-ai-agent",
            daemon=True,
        )
        _THREAD.start()
        return True


def background_worker_status() -> dict[str, Any]:
    thread = _THREAD
    return {
        **_STATE,
        "alive": bool(thread and thread.is_alive()),
    }
