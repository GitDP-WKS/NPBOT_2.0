from __future__ import annotations

import pandas as pd
import streamlit as st

from .import_service import import_plan
from .importer import FIELD_LABELS, inspect_excel
from .quality import dashboard
from .repositories import backup_snapshot, browse_knowledge
from .structure import CURRENT_STRUCTURE
from .ui_labels import source_kind_label, status_label


def page_home() -> None:
    st.header("Главная")
    values = dashboard()
    cols = st.columns(4)
    cols[0].metric("Адресов", values["addresses"])
    cols[1].metric("Связей адрес–РЭС", values["mappings"])
    cols[2].metric("Под вопросом", values["open_tasks"])
    cols[3].metric("Противоречий", values["conflicts"])
    cols = st.columns(4)
    cols[0].metric("Проверено человеком", values["human_verified"])
    cols[1].metric("Размеченных текстов", values["text_examples"])
    cols[2].metric("Статус модели", values["model_status"])
    cols[3].metric(
        "Точность модели",
        "—" if values["model_accuracy"] is None else f"{values['model_accuracy']:.2f}%",
    )


def _preview(plan) -> pd.DataFrame:
    rows = []
    for sheet in plan.sheets:
        rows.extend(sheet.rows[:20])
    columns = ["branch", "res", "locality", "district", "settlement", "street", "text"]
    labels = {
        "branch": "Филиал",
        "res": "РЭС",
        "locality": "Населенный пункт",
        "district": "Район",
        "settlement": "СНТ / поселок",
        "street": "Улица",
        "text": "Исходный текст",
    }
    return pd.DataFrame([{labels[key]: row.get(key, "") for key in columns} for row in rows])


def page_upload() -> None:
    st.header("Загрузка")
    st.write(
        "Загрузите любой Excel. Система сама ищет заголовки и определяет назначение столбцов по названиям и содержимому."
    )
    uploaded = st.file_uploader("Excel-файл", type=["xlsx", "xls"])
    if not uploaded:
        return
    content = uploaded.getvalue()
    try:
        plan = inspect_excel(content, uploaded.name)
    except Exception as exc:
        st.error(str(exc))
        return
    st.success(f"Распознано строк: {plan.detected_rows}. Тип данных: {source_kind_label(plan.source_kind)}.")
    if plan.warnings:
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
    if st.button("Сохранить в постоянную базу и проанализировать", type="primary", use_container_width=True):
        try:
            with st.spinner("Сохраняю данные и формирую сомнения..."):
                result = import_plan(plan)
        except Exception as exc:
            st.error(f"Ошибка загрузки: {exc}")
            return
        if result["already_loaded"]:
            st.info("Этот файл уже был загружен. Счетчики и обучение не изменены.")
        else:
            cols = st.columns(4)
            cols[0].metric("Распознано", result["seen"])
            cols[1].metric("Добавлено", result["imported"])
            cols[2].metric("Дубли внутри файла", result["duplicates"])
            cols[3].metric("Требуют уточнения", result["issues"])
            st.success("Данные сохранены в Neon и автоматически проанализированы агентом.")


def page_knowledge() -> None:
    st.header("База знаний")
    cols = st.columns(4)
    search = cols[0].text_input("Поиск по адресу")
    statuses = ["", "source_only", "consistent", "human_verified", "conflict", "rejected"]
    status = cols[1].selectbox(
        "Статус",
        statuses,
        format_func=lambda value: "Все статусы" if value == "" else status_label(value),
    )
    branch = cols[2].selectbox(
        "Филиал",
        [""] + sorted(set(CURRENT_STRUCTURE.values())),
        format_func=lambda value: "Все филиалы" if value == "" else value,
    )
    res = cols[3].selectbox(
        "РЭС",
        [""] + list(CURRENT_STRUCTURE),
        format_func=lambda value: "Все РЭС" if value == "" else value,
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
        "source_confidence": "Доверие источнику",
        "human_confirmations": "Подтверждений",
    }
    if rows:
        frame = pd.DataFrame(rows).rename(columns=rename)
        frame["Статус"] = frame["Статус"].map(status_label)
        frame["Доверие источнику"] = frame["Доверие источнику"].map(
            lambda value: f"{float(value):.1f}%"
        )
        frame = frame[list(rename.values())]
    else:
        frame = pd.DataFrame(columns=list(rename.values()))
    st.dataframe(frame, use_container_width=True, hide_index=True)


def page_settings() -> None:
    st.header("Настройки")
    st.subheader("Резервная копия")
    st.write(
        "Копия содержит базу знаний, источники, задания, голоса, решения, модели, журнал и очередь агента."
    )
    st.download_button(
        "Скачать полную резервную копию",
        backup_snapshot(),
        "res_ai_v2_backup.json",
        "application/json",
        use_container_width=True,
    )
    st.info(
        "Перенос данных старой версии убран из ежедневного интерфейса. Он не запускается случайным нажатием и не изменяет рабочую базу."
    )
