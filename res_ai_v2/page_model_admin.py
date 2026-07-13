from __future__ import annotations

import pandas as pd
import streamlit as st

from .analyzer import analyze_database
from .modeling import list_model_versions, publish_candidate, rollback_model, train_candidate
from .quality import quality_details


def _metrics(result) -> None:
    metrics = result["metrics"]; cols = st.columns(4)
    cols[0].metric("Контрольная точность", f"{metrics['accuracy']*100:.2f}%"); cols[1].metric("Macro-F1", f"{metrics['macro_f1']*100:.2f}%"); cols[2].metric("Примеров", metrics["rows"]); cols[3].metric("Классов РЭС", metrics["classes"])
    if result.get("gate_passed"): st.success("Кандидат прошел порог качества.")
    else:
        for reason in result.get("gate_reasons", []): st.warning(reason)
    per_res = [{"РЭС": res, "Точность": round(values["precision"]*100,2), "Полнота": round(values["recall"]*100,2), "F1": round(values["f1"]*100,2), "Примеров": values["support"]} for res, values in metrics.get("per_res", {}).items()]
    if per_res: st.dataframe(pd.DataFrame(per_res).sort_values("Полнота"), use_container_width=True, hide_index=True)
    if result.get("confusion"): st.subheader("Наиболее частые ошибки"); st.dataframe(pd.DataFrame(result["confusion"]), use_container_width=True, hide_index=True)


def page_training() -> None:
    st.header("Анализ и обучение")
    st.write("Анализ ищет противоречия и недостающий район. Обучение использует только однозначные связи и размеченные тексты; многозначные адреса исключаются.")
    a, b, c = st.columns(3)
    if a.button("Проанализировать базу", use_container_width=True):
        with st.spinner("Проверяю базу..."): result = analyze_database()
        st.success(f"Сформировано заданий: {result['tasks']}; конфликтов: {result['conflicts']}; без района: {result['missing_context']}.")
    if b.button("Обучить кандидата", use_container_width=True):
        try:
            with st.spinner("Обучаю и проверяю модель..."): st.session_state["last_training"] = train_candidate()
        except Exception as exc: st.error(str(exc))
    if c.button("Полный цикл", type="primary", use_container_width=True):
        try:
            with st.spinner("Анализирую базу и обучаю кандидата..."): analyze_database(); st.session_state["last_training"] = train_candidate()
        except Exception as exc: st.error(str(exc))
    if st.session_state.get("last_training"): _metrics(st.session_state["last_training"])
    versions = list_model_versions()
    if versions:
        st.subheader("Версии модели")
        rows = [{"Версия": row["version"], "Статус": row["status"], "Порог пройден": row["gate_passed"], "Точность": round(row["metrics"].get("accuracy",0)*100,2), "Macro-F1": round(row["metrics"].get("macro_f1",0)*100,2), "Создана": row["created_at"]} for row in versions]
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        version = st.selectbox("Версия", [row["version"] for row in versions])
        left, right = st.columns(2)
        if left.button("Опубликовать кандидата", use_container_width=True):
            try: publish_candidate(version); st.success("Модель опубликована."); st.rerun()
            except Exception as exc: st.error(str(exc))
        if right.button("Откатить рабочую модель к версии", use_container_width=True):
            try: rollback_model(version); st.success("Рабочая модель переключена."); st.rerun()
            except Exception as exc: st.error(str(exc))


def page_quality() -> None:
    st.header("Качество")
    values = quality_details()
    st.subheader("Состояние данных")
    st.dataframe(pd.DataFrame([{"Статус": key, "Записей": value} for key, value in values["mapping_status"].items()]), use_container_width=True, hide_index=True)
    st.subheader("Открытые задания")
    st.dataframe(pd.DataFrame([{"Тип": key, "Заданий": value} for key, value in values["open_by_type"].items()]), use_container_width=True, hide_index=True)
    cols = st.columns(3); cols[0].metric("Загруженных файлов", values["file_count"]); cols[1].metric("Размеченных текстов", values["text_count"]); cols[2].metric("Запусков определения", values["prediction_count"])
