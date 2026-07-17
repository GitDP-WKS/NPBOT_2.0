from __future__ import annotations

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
from .ui_runtime import reset_runtime_error, runtime_status, start_runtime_async

BUILD_ID = "2026.07.16.2"


def _initialize_runtime() -> None:
    initialize_database()
    start_background_worker()


def _render_database_state(page: str, status: dict) -> None:
    st.header(page)
    if status.get("error"):
        st.error("Нет подключения к общей базе Neon.")
        st.caption("Интерфейс продолжает работать. Проверьте DATABASE_URL в Secrets.")
        with st.expander("Техническая причина"):
            st.code(status["error"])
        if st.button("Повторить подключение", width="stretch", key=f"retry_{page}"):
            reset_runtime_error()
            start_runtime_async(_initialize_runtime)
            st.rerun()
        return
    st.info("Подключение к базе выполняется в фоне. Можно перейти в «Загрузка» или «Определение».")
    if st.button("Обновить состояние", width="stretch", key=f"refresh_{page}"):
        st.rerun()


def main() -> None:
    st.set_page_config(page_title="РЭС AI 2.0", page_icon="⚡", layout="wide")
    configure()
    style()
    st.title("РЭС AI")
    st.caption("Определение филиала и РЭС по Республике Татарстан")

    start_runtime_async(_initialize_runtime)
    status = runtime_status()

    is_admin = admin_login()
    reviewer = "Администратор" if is_admin else st.sidebar.text_input(
        "Проверяющий", placeholder="Фамилия и имя"
    )
    pages = ["Проверка", "Определение"]
    if is_admin:
        pages = [
            "Главная", "Проверка", "Определение", "Загрузка", "База знаний",
            "Анализ и обучение", "Качество", "Центр управления", "Журнал", "Настройки",
        ]
    st.sidebar.caption(f"Сборка {BUILD_ID}")
    if status.get("ready"):
        st.sidebar.success("Общая база: PostgreSQL / Neon")
    elif status.get("error"):
        st.sidebar.error("Neon недоступен")
    else:
        st.sidebar.info("Подключение к Neon...")
    page = st.sidebar.radio("Раздел", pages, key="main_navigation")

    database_ready = bool(status.get("ready"))
    if page == "Загрузка":
        page_upload(database_ready=database_ready)
        return
    if page == "Определение":
        page_predict(database_ready=database_ready)
        return
    if not database_ready:
        _render_database_state(page, status)
        return

    handlers = {
        "Главная": page_home,
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
