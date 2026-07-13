from __future__ import annotations

import streamlit as st

from .db import initialize_database, storage_name
from .page_data_admin import page_home, page_journal, page_knowledge, page_settings, page_upload
from .page_model_admin import page_quality, page_training
from .page_predict import page_predict
from .page_review import page_review
from .ui_common import admin_login, configure, style


def main() -> None:
    st.set_page_config(page_title="РЭС AI 2.0", page_icon="⚡", layout="wide")
    configure(); initialize_database(); style()
    st.title("РЭС AI")
    st.caption("Система определения филиала и РЭС по адресам Республики Татарстан")
    is_admin = admin_login()
    reviewer = "Администратор" if is_admin else st.sidebar.text_input("Проверяющий", placeholder="Фамилия и имя")
    pages = ["Проверка", "Определение"]
    if is_admin: pages = ["Главная", "Проверка", "Определение", "Загрузка", "База знаний", "Анализ и обучение", "Качество", "Журнал", "Настройки"]
    st.sidebar.caption(f"Хранилище: {storage_name()}")
    page = st.sidebar.radio("Раздел", pages)
    handlers = {"Главная": page_home, "Определение": page_predict, "Загрузка": page_upload, "База знаний": page_knowledge, "Анализ и обучение": page_training, "Качество": page_quality, "Журнал": page_journal, "Настройки": page_settings}
    if page == "Проверка": page_review(is_admin, reviewer)
    else: handlers[page]()
