from __future__ import annotations

import os
import threading
import time
from typing import Any

from .agent import run_agent_cycle
from .daily_audit import ensure_daily_audit

_LOCK = threading.Lock()
_THREAD: threading.Thread | None = None
_STATE: dict[str, Any] = {
    "running": False,
    "last_error": "",
    "processed": 0,
    "completed": 0,
    "failed": 0,
}


def _loop() -> None:
    _STATE["running"] = True
    while True:
        try:
            ensure_daily_audit()
            result = run_agent_cycle(max_events=25, worker_id="background-worker")
            _STATE["processed"] += result.processed
            _STATE["completed"] += result.completed
            _STATE["failed"] += result.failed
            _STATE["last_error"] = ""
            time.sleep(0.5 if result.processed else 3.0)
        except Exception as exc:
            _STATE["last_error"] = str(exc)[:1000]
            time.sleep(5.0)


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
