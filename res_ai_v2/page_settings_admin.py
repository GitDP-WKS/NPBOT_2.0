from __future__ import annotations

import streamlit as st

from .backup_service import backup_snapshot
from .db import storage_name


def page_settings() -> None:
    st.header("Настройки")
    st.subheader("Подключение")
    st.success(f"Общая база: {storage_name()}")

    st.subheader("Резервная копия")
    st.download_button(
        "Скачать полную копию",
        backup_snapshot(),
        "res_ai_full_backup.json",
        "application/json",
        use_container_width=True,
    )
