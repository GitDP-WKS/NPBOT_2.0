from __future__ import annotations

import pandas as pd
import streamlit as st

from .agent import run_agent_cycle
from .agent_monitor import agent_status, recent_agent_events, recent_agent_runs, recover_stale_events
from .diagnostics import run_diagnostics


def page_agent_center() -> None:
    st.header("Центр управления агентом")
    diagnostics = run_diagnostics()
    status = diagnostics["agent"]
    counts = status["counts"]

    cols = st.columns(5)
    cols[0].metric("Ожидают обработки", counts.get("pending", 0))
    cols[1].metric("Обрабатываются", counts.get("processing", 0))
    cols[2].metric("Повторная попытка", counts.get("retry", 0))
    cols[3].metric("Завершены", counts.get("completed", 0))
    cols[4].metric("Ошибки", counts.get("failed", 0))

    if diagnostics["healthy"]:
        st.success("Система и агент работают штатно.")
    else:
        st.warning("Самодиагностика обнаружила проблемы.")
        st.dataframe(
            pd.DataFrame(
                [
                    {"Проблема": item["title"], "Количество": item["count"]}
                    for item in diagnostics["problems"]
                ]
            ),
            use_container_width=True,
            hide_index=True,
        )

    left, right = st.columns(2)
    if left.button("Обработать очередь сейчас", type="primary", use_container_width=True):
        with st.spinner("Агент обрабатывает события..."):
            result = run_agent_cycle(max_events=200, worker_id="streamlit-admin")
        st.success(
            f"Обработано: {result.processed}; завершено: {result.completed}; ошибок: {result.failed}."
        )
        st.rerun()

    if right.button("Вернуть зависшие события в очередь", use_container_width=True):
        recovered = recover_stale_events()
        if recovered:
            st.success(f"Возвращено событий: {recovered}.")
        else:
            st.info("Зависших событий не найдено.")
        st.rerun()

    tab_events, tab_runs = st.tabs(["События", "Запуски агента"])
    with tab_events:
        rows = recent_agent_events()
        if not rows:
            st.info("Событий пока нет.")
        else:
            frame = pd.DataFrame(
                [
                    {
                        "Номер": row["id"],
                        "Событие": row["event_name"],
                        "Объект": row["subject_key"],
                        "Состояние": row["status_name"],
                        "Попыток": row["attempts"],
                        "Последняя ошибка": row["last_error"],
                        "Создано": row["created_at"],
                        "Обновлено": row["updated_at"],
                    }
                    for row in rows
                ]
            )
            st.dataframe(frame, use_container_width=True, hide_index=True)

    with tab_runs:
        rows = recent_agent_runs()
        if not rows:
            st.info("Запусков пока нет.")
        else:
            frame = pd.DataFrame(
                [
                    {
                        "Номер запуска": row["id"],
                        "Событие": row["event_name"],
                        "Объект": row["subject_key"],
                        "Исполнитель": row["worker_id"],
                        "Состояние": row["status_name"],
                        "Ошибка": row["error_text"],
                        "Начало": row["started_at"],
                        "Завершение": row["finished_at"],
                    }
                    for row in rows
                ]
            )
            st.dataframe(frame, use_container_width=True, hide_index=True)
