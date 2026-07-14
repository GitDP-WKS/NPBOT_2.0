from __future__ import annotations

import streamlit as st

from .db import storage_name
from .repositories import backup_snapshot


def page_settings() -> None:
    st.header("Настройки")
    st.subheader("Подключение")
    st.success(f"Общее хранилище: {storage_name()}")
    st.caption("Все пользователи работают с одной базой Neon через один адрес приложения.")

    st.subheader("Резервная копия")
    st.write(
        "Резервная копия содержит базу знаний, решения людей, задания, версии модели, события агента и журнал."
    )
    st.download_button(
        "Скачать полную резервную копию",
        backup_snapshot(),
        "res_ai_v2_backup.json",
        "application/json",
        use_container_width=True,
    )
