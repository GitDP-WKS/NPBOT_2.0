from __future__ import annotations

import streamlit as st

from .backup_service import backup_snapshot


def page_settings() -> None:
    st.header("Настройки")
    st.subheader("Резервная копия")
    snapshot = backup_snapshot()
    st.download_button(
        "Скачать полную копию",
        snapshot,
        "res_ai_full_backup.json",
        "application/json",
        width="stretch",
    )
