from __future__ import annotations

import threading
from typing import Any

import streamlit as st

from .background_worker import start_background_worker
from .db import initialize_database
from .page_agent_admin import page_agent_center
from .page_data_admin import page_home, page_knowledge, page_upload
from .page_journal import page_journal
from .page_model_admin import page_quality, page_training
from .page_predict import page_predict
from .page_review import page_review
from .page_settings_admin import page_settings
from .ui_common import admin_login, configure, style

_RUNTIME_LOCK = threading.Lock()
_RUNTIME_THREAD: threading.Thread | None = None
_RUNTIME_STATE: dict[str, Any] = {
    "ready": False,
    "running": False,
    "error": "",
}


def _runtime_bootstrap() -> None:
    _RUNTIME_STATE["running"] = True
    _RUNTIME_STATE["error"] = ""
    try:
        initialize_database()
        start_background_worker()
        _RUNTIME_STATE["ready"] = True
    except Exception as exc:
        _RUNTIME_STATE["error"] = str(exc)[:2000]
    finally:
        _RUNTIME_STATE["running"] = False


def _start_runtime_async() -> None:
    """Запускает подготовку базы и агента без блокировки интерфейса."""
    global _RUNTIME_THREAD
    if _RUNTIME_STATE["ready"]:
        return
    with _RUNTIME_LOCK:
        if _RUNTIME_THREAD and _RUNTIME_THREAD.is_alive():
            return
        _RUNTIME_THREAD = threading.Thread(
            target=_runtime_bootstrap,
            name="res-ai-runtime-bootstrap",
            daemon=True,
        )
        _RUNTIME_THREAD.start()


def runtime_status() -> dict[str, Any]:
    return dict(_RUNTIME_STATE)


def _page_title(page: str) -> str:
    return {
        "Главная": "Главная",
        "Проверка": "Проверка",
        "Определение": "Определение филиала и РЭС",
        "Загрузка": "Загрузка",
        "База знаний": "База знаний",
        "Анализ и обучение": "Анализ и обучение",
        "Качество": "Качество",
        "Центр управления": "Центр управления",
        "Журнал": "Журнал",
        "Настройки": "Настройки",
    }[page]


def _render_runtime_state(page: str, status: dict[str, Any]) -> None:
    """Показывает состояние запуска внутри реально выбранного раздела."""
    st.header(_page_title(page))
    if status["error"]:
        st.error("Не удалось подключить единую базу Neon.")
        st.code(status["error"])
        if st.button("Повторить подключение", use_container_width=True, key=f"retry_{page}"):
            _RUNTIME_STATE["error"] = ""
            _start_runtime_async()
            st.rerun()
        return

    st.info("Подключаю базу данных. Раздел откроется автоматически.")

    @st.fragment(run_every=1.0)
    def _watch_runtime() -> None:
        current = runtime_status()
        if current["ready"] or current["error"]:
            st.rerun()

    _watch_runtime()


def main() -> None:
    st.set_page_config(page_title="РЭС AI 2.0", page_icon="⚡", layout="wide")
    configure()
    style()
    st.title("РЭС AI")
    st.caption("Определение филиала и РЭС по Республике Татарстан")

    _start_runtime_async()

    is_admin = admin_login()
    reviewer = (
        "Администратор"
        if is_admin
        else st.sidebar.text_input("Проверяющий", placeholder="Фамилия и имя")
    )
    pages = ["Проверка", "Определение"]
    if is_admin:
        pages = [
            "Главная",
            "Проверка",
            "Определение",
            "Загрузка",
            "База знаний",
            "Анализ и обучение",
            "Качество",
            "Центр управления",
            "Журнал",
            "Настройки",
        ]
    page = st.sidebar.radio("Раздел", pages, key="main_navigation")

    status = runtime_status()
    if not status["ready"]:
        _render_runtime_state(page, status)
        return

    handlers = {
        "Главная": page_home,
        "Определение": page_predict,
        "Загрузка": page_upload,
        "База знаний": page_knowledge,
        "Анализ и обучение": page_training,
        "Качество": page_quality,
        "Центр управления": page_agent_center,
        "Журнал": page_journal,
        "Настройки": page_settings,
    }
    if page == "Проверка":
        page_review(is_admin, reviewer)
    else:
        handlers[page]()
