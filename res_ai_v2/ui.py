from __future__ import annotations

import streamlit as st

from .background_worker import start_background_worker
from .db import initialize_database, storage_name
from .page_agent_admin import page_agent_center
from .page_data_admin import page_home, page_knowledge, page_upload
from .page_journal import page_journal
from .page_model_admin import page_quality, page_training
from .page_predict import page_predict
from .page_review import page_review
from .page_settings_admin import page_settings
from .ui_common import admin_login, configure, style


@st.cache_resource(show_spinner=False)
def _initialize_runtime() -> bool:
    """Инициализирует БД и фоновый агент один раз на процесс Streamlit."""
    initialize_database()
    start_background_worker()
    return True


def main() -> None:
    st.set_page_config(page_title="РЭС AI 2.0", page_icon="⚡", layout="wide")
    configure()
    style()
    st.title("РЭС AI")
    st.caption("Определение филиала и РЭС по Республике Татарстан")

    try:
        _initialize_runtime()
    except Exception as exc:
        st.error("Общая база данных недоступна.")
        st.code(str(exc))
        st.stop()

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
    st.sidebar.success(f"Общая база: {storage_name()}")
    page = st.sidebar.radio("Раздел", pages)
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
