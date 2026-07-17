from __future__ import annotations

import os
from typing import Any

import pandas as pd
import streamlit as st

from .display_names import short_executor_name

ORDER = ["Филиал", "РЭС", "Населенный пункт", "Район", "СНТ / поселок", "Улица"]
FIELDS = {
    "Филиал": "branch",
    "РЭС": "res",
    "Населенный пункт": "locality",
    "Район": "district",
    "СНТ / поселок": "settlement",
    "Улица": "street",
}


def configure() -> None:
    try:
        if st.secrets.get("DATABASE_URL"):
            os.environ["DATABASE_URL"] = str(st.secrets["DATABASE_URL"])
        if st.secrets.get("ADMIN_PASSWORD"):
            os.environ["ADMIN_PASSWORD"] = str(st.secrets["ADMIN_PASSWORD"])
    except Exception:
        pass


def style() -> None:
    st.markdown(
        """<style>
        .block-container{max-width:1450px;padding-top:1.1rem}
        [data-testid="stMetric"]{border:1px solid rgba(128,128,128,.35);border-radius:14px;padding:14px;background:rgba(128,128,128,.06)}
        div[data-testid="stDataFrame"]{border:1px solid rgba(128,128,128,.35);border-radius:12px}
        div.stButton>button{border-radius:10px;font-weight:650;min-height:42px}
        div[data-baseweb="select"] *, input, textarea{color:inherit!important}
        </style>""",
        unsafe_allow_html=True,
    )


def admin_login() -> bool:
    if st.session_state.get("is_admin"):
        if st.sidebar.button("Выйти из служебного режима", width="stretch"):
            st.session_state["is_admin"] = False
            st.session_state["main_navigation"] = "Проверка"
            st.rerun()
        return True
    with st.sidebar.expander("Служебный вход"):
        with st.form("admin_login_form"):
            value = st.text_input("Пароль", type="password", label_visibility="collapsed")
            submitted = st.form_submit_button("Войти", width="stretch")
        if submitted:
            expected = os.getenv("ADMIN_PASSWORD", "")
            if expected and value == expected:
                st.session_state["is_admin"] = True
                st.session_state["main_navigation"] = "Загрузка"
                st.rerun()
            else:
                st.error("Неверный пароль.")
    return False


def address_table(rows: list[dict[str, Any]]) -> pd.DataFrame:
    table_rows = []
    for row in rows:
        values = {
            title: row.get(
                field,
                row.get({"branch": "branch_name", "res": "res_name"}.get(field, field), ""),
            )
            for title, field in FIELDS.items()
        }
        values["Филиал"] = short_executor_name(str(values["Филиал"]))
        values["РЭС"] = short_executor_name(str(values["РЭС"]))
        table_rows.append(values)
    return pd.DataFrame(table_rows, columns=ORDER)


def display_options(rows: list[dict[str, Any]]) -> None:
    if rows:
        st.dataframe(address_table(rows), width="stretch", hide_index=True)


def flash() -> None:
    message = st.session_state.pop("flash", None)
    if message:
        st.success(message)
