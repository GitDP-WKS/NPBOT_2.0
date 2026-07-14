from __future__ import annotations

import pandas as pd
import streamlit as st

from .display_names import short_executor_name
from .import_service import import_plan
from .importer import FIELD_LABELS, inspect_excel
from .quality import dashboard
from .repositories import browse_knowledge
from .structure import CURRENT_STRUCTURE
from .ui_labels import source_kind_label, status_label


def page_home() -> None:
    st.header("Главная")
    values = dashboard()
    cols = st.columns(4)
    cols[0].metric("Адресов", values["addresses"])
    cols[1].metric("Связей адрес–РЭС", values["mappings"])
    cols[2].metric("На проверке", values["open_tasks"])
    cols[3].metric("Противоречий", values["conflicts"])
    cols = st.columns(4)
    cols[0].metric("Решений людей", values["human_verified"])
    cols[1].metric("Текстов", values["text_examples"])
    cols[2].metric("Модель", values["model_status"])
    cols[3].metric(
        "Точность",
        "—" if values["model_accuracy"] is None else f"{values['model_accuracy']:.2f}%",
    )


def _preview(plan) -> pd.DataFrame:
    rows = []
    for sheet in plan.sheets:
        rows.extend(sheet.rows[:20])
    result = pd.DataFrame(
        [
            {
                "Филиал": short_executor_name(str(row.get("branch", ""))),
                "РЭС": short_executor_name(str(row.get("res", ""))),
                "Населенный пункт": row.get("locality", ""),
                "Район": row.get("district", ""),
                "СНТ / поселок": row.get("settlement", ""),
                "Улица": row.get("street", ""),
                "Исходный текст": row.get("text", ""),
            }
            for row in rows
        ]
    )
    return result


def page_upload() -> None:
    st.header("Загрузка")
    st.caption("Загрузите Excel. Столбцы система распознает сама.")
    uploaded = st.file_uploader("Excel-файл", type=["xlsx", "xls"])
    if not uploaded:
        return
    try:
        plan = inspect_excel(uploaded.getvalue(), uploaded.name)
    except Exception as exc:
        st.error(str(exc))
        return
    st.success(f"Распознано: {plan.detected_rows}. Тип: {source_kind_label(plan.source_kind)}.")
    for warning in plan.warnings:
        st.warning(warning)
    for sheet in plan.sheets:
        with st.expander(f"Лист: {sheet.sheet_name}", expanded=bool(sheet.warnings)):
            rows = [
                {
                    "Столбец Excel": source,
                    "Поле РЭС AI": FIELD_LABELS.get(target, target),
                    "Уверенность": round(sheet.confidence.get(target, 0) * 100, 1),
                }
                for source, target in sheet.columns.items()
            ]
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    st.dataframe(_preview(plan), use_container_width=True, hide_index=True)
    if st.button("Загрузить", type="primary", use_container_width=True):
        try:
            with st.spinner("Сохраняю файл..."):
                result = import_plan(plan, wait_for_agent=False)
        except Exception as exc:
            st.error(f"Ошибка загрузки: {exc}")
            return
        if result["already_loaded"]:
            st.info("Этот файл уже загружен.")
            return
        cols = st.columns(3)
        cols[0].metric("Строк", result["seen"])
        cols[1].metric("Новых", result["imported"])
        cols[2].metric("Повторов", result["duplicates"])
        st.success("Файл сохранен. Агент анализирует его в фоне.")


def page_knowledge() -> None:
    st.header("База знаний")
    cols = st.columns(4)
    search = cols[0].text_input("Поиск")
    statuses = ["", "source_only", "consistent", "human_verified", "conflict", "rejected"]
    status = cols[1].selectbox(
        "Статус",
        statuses,
        format_func=lambda value: "Все" if value == "" else status_label(value),
    )
    branch = cols[2].selectbox(
        "Филиал",
        [""] + sorted(set(CURRENT_STRUCTURE.values())),
        format_func=lambda value: "Все" if value == "" else short_executor_name(value),
    )
    res = cols[3].selectbox(
        "РЭС",
        [""] + list(CURRENT_STRUCTURE),
        format_func=lambda value: "Все" if value == "" else short_executor_name(value),
    )
    rows = browse_knowledge(search, status, branch, res)
    rename = {
        "branch_name": "Филиал",
        "res_name": "РЭС",
        "locality": "Населенный пункт",
        "district": "Район",
        "settlement": "СНТ / поселок",
        "street": "Улица",
        "status": "Статус",
        "source_confidence": "Доверие",
        "human_confirmations": "Решений",
    }
    if rows:
        frame = pd.DataFrame(rows).rename(columns=rename)
        frame["Филиал"] = frame["Филиал"].map(short_executor_name)
        frame["РЭС"] = frame["РЭС"].map(short_executor_name)
        frame["Статус"] = frame["Статус"].map(status_label)
        frame["Доверие"] = frame["Доверие"].map(lambda value: f"{float(value):.1f}%")
        frame = frame[list(rename.values())]
    else:
        frame = pd.DataFrame(columns=list(rename.values()))
    st.dataframe(frame, use_container_width=True, hide_index=True)
