from __future__ import annotations

import streamlit as st

from .db import get_setting
from .search import load_search_index, predict
from .ui_common import display_options


@st.cache_data(show_spinner=False)
def cached_index(data_version: str):
    del data_version
    return load_search_index()


def page_predict(*, database_ready: bool = True) -> None:
    st.header("Определение филиала и РЭС")
    text = st.text_area(
        "Адрес или текст",
        height=140,
        placeholder="Например: Лаишевский район, село Усады, нет электричества",
    )
    if not st.button("Определить", type="primary", width="stretch"):
        return
    if not database_ready:
        st.warning("База данных еще подключается. Повторите определение через несколько секунд.")
        return
    with st.spinner("Анализирую адрес..."):
        result = predict(text, index=cached_index(get_setting("data_version", "1")), enqueue=True)
    if result.status == "not_found":
        st.warning(result.reason)
    elif result.status == "ambiguous":
        st.warning(result.reason)
    elif result.status == "preliminary":
        st.info(result.reason)
    else:
        st.success(result.reason)
    cols = st.columns(3)
    cols[0].metric("Статус", {"final":"Определено", "ambiguous":"Нужно уточнение", "preliminary":"Предварительно", "not_found":"Не найдено"}.get(result.status, result.status))
    cols[1].metric("Уверенность", f"{result.confidence:.1f}%")
    cols[2].metric("Метод", {"address_rules":"Адресная база", "human_mapping":"Решение человека", "human_rule":"Правило человека", "model":"Модель", "none":"Нет результата"}.get(result.method, result.method))
    display_options(result.candidates)
    if result.needs_review:
        st.caption("Случай автоматически добавлен в общую очередь проверки.")
