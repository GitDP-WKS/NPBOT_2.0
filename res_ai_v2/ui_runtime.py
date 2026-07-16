from __future__ import annotations

import threading
from typing import Any, Callable

_RUNTIME_LOCK = threading.Lock()
_RUNTIME_THREAD: threading.Thread | None = None
_RUNTIME_STATE: dict[str, Any] = {
    "ready": False,
    "running": False,
    "error": "",
}


def runtime_status() -> dict[str, Any]:
    return dict(_RUNTIME_STATE)


def start_runtime_async(initializer: Callable[[], None]) -> None:
    global _RUNTIME_THREAD
    if _RUNTIME_STATE["ready"]:
        return
    with _RUNTIME_LOCK:
        if _RUNTIME_THREAD and _RUNTIME_THREAD.is_alive():
            return

        def run() -> None:
            _RUNTIME_STATE["running"] = True
            _RUNTIME_STATE["error"] = ""
            try:
                initializer()
                _RUNTIME_STATE["ready"] = True
            except Exception as exc:
                _RUNTIME_STATE["error"] = str(exc)[:2000]
            finally:
                _RUNTIME_STATE["running"] = False

        _RUNTIME_THREAD = threading.Thread(
            target=run,
            name="res-ai-runtime-bootstrap",
            daemon=True,
        )
        _RUNTIME_THREAD.start()


def reset_runtime_error() -> None:
    _RUNTIME_STATE["error"] = ""
