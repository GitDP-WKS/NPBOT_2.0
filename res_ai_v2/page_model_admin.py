from __future__ import annotations

import pandas as pd
import streamlit as st

from .admin_service import request_analysis_and_training, request_full_analysis, request_training
from .modeling import list_model_versions, publish_candidate, rollback_model
from .quality import quality_details
from .ui_labels import status_label, task_type_label


def _metrics(result) -> None:
    metrics = result["metrics"]
    cols = st.columns(4)
    cols[0].metric("Контрольная точность", f"{metrics['accuracy'] * 100:.2f}%")
    cols[1].metric("Средняя оценка по всем РЭС", f"{metrics['macro_f1'] * 100:.2f}%")
    cols[2].metric("Примеров", metrics["rows"])
    cols[3].metric("РЭС в обучении", metrics["classes"])
    if result.get("gate_passed"):
        st.success("Кандидат прошел проверку качества.")
    else:
        for reason in metrics.get("gate_reasons", []):
            st.warning(reason)
    per_res = [
        {
            "РЭС": res,
            "Точность ответов": round(values["precision"] * 100, 2),
            "Найдено правильных случаев": round(values["recall"] * 100, 2),
            "Итоговая оценка": round(values["f1"] * 100, 2),
            "Примеров": values["support"],
        }
        for res, values in metrics.get("per_res", {}).items()
    ]
    if per_res:
        st.dataframe(
            pd.DataFrame(per_res).sort_values("Найдено правильных случаев"),
            use_container_width=True,
            hide_index=True,
        )
    if result.get("confusion"):
        st.subheader("Наиболее частые ошибки")
        frame = pd.DataFrame(result["confusion"]).rename(
            columns={"true": "Правильный РЭС", "predicted": "Выбранный моделью РЭС", "count": "Количество ошибок"}
        )
        st.dataframe(frame, use_container_width=True, hide_index=True)


def _show_agent_failure(payload: dict) -> bool:
    agent = payload.get("agent") or {}
    if int(agent.get("failed", 0)) > 0:
        st.error("Операция не завершена. Подробности сохранены в Центре управления агентом.")
        return True
    return False


def page_training() -> None:
    st.header("Анализ и обучение")
    st.write(
        "Все операции выполняет единый агент. Анализ формирует задания человеку, а обучение создает кандидата модели. "
        "Рабочая модель меняется только после решения администратора."
    )
    a, b, c = st.columns(3)
    if a.button("Проанализировать базу", use_container_width=True):
        with st.spinner("Агент проверяет базу..."):
            response = request_full_analysis()
        if not _show_agent_failure(response):
            result = (response.get("result") or {}).get("analysis") or {}
            st.success(
                f"Сформировано заданий: {result.get('tasks', 0)}; "
                f"противоречий: {result.get('conflicts', 0)}; "
                f"адресов без района: {result.get('missing_context', 0)}."
            )
    if b.button("Подготовить новую модель", use_container_width=True):
        try:
            with st.spinner("Агент обучает и проверяет кандидата..."):
                response = request_training()
            if not _show_agent_failure(response):
                st.session_state["last_training"] = response.get("result")
        except Exception as exc:
            st.error(str(exc))
    if c.button("Полный цикл", type="primary", use_container_width=True):
        try:
            with st.spinner("Агент анализирует базу и готовит кандидата..."):
                response = request_analysis_and_training()
            analysis = response.get("analysis") or {}
            training = response.get("training") or {}
            if not _show_agent_failure(analysis) and training and not _show_agent_failure(training):
                st.session_state["last_training"] = training.get("result")
                st.success("Анализ завершен, кандидат модели подготовлен.")
        except Exception as exc:
            st.error(str(exc))
    if st.session_state.get("last_training"):
        _metrics(st.session_state["last_training"])
    versions = list_model_versions()
    if versions:
        st.subheader("Версии модели")
        rows = [
            {
                "Версия": row["version"],
                "Статус": status_label(row["status"]),
                "Проверка качества пройдена": "Да" if row["gate_passed"] else "Нет",
                "Точность": round(row["metrics"].get("accuracy", 0) * 100, 2),
                "Средняя оценка по всем РЭС": round(row["metrics"].get("macro_f1", 0) * 100, 2),
                "Создана": row["created_at"],
            }
            for row in versions
        ]
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        version = st.selectbox("Выберите версию", [row["version"] for row in versions])
        left, right = st.columns(2)
        if left.button("Сделать выбранную версию рабочей", use_container_width=True):
            try:
                publish_candidate(version)
                st.success("Выбранная версия стала рабочей.")
                st.rerun()
            except Exception as exc:
                st.error(str(exc))
        if right.button("Вернуться к выбранной версии", use_container_width=True):
            try:
                rollback_model(version)
                st.success("Рабочая модель переключена на выбранную версию.")
                st.rerun()
            except Exception as exc:
                st.error(str(exc))


def page_quality() -> None:
    st.header("Качество")
    values = quality_details()
    st.subheader("Состояние данных")
    status_rows = [
        {"Статус": status_label(key), "Записей": value}
        for key, value in values["mapping_status"].items()
    ]
    st.dataframe(pd.DataFrame(status_rows), use_container_width=True, hide_index=True)
    st.subheader("Открытые задания")
    task_rows = [
        {"Тип": task_type_label(key), "Заданий": value}
        for key, value in values["open_by_type"].items()
    ]
    st.dataframe(pd.DataFrame(task_rows), use_container_width=True, hide_index=True)
    cols = st.columns(3)
    cols[0].metric("Загруженных файлов", values["file_count"])
    cols[1].metric("Размеченных текстов", values["text_count"])
    cols[2].metric("Запусков определения", values["prediction_count"])
