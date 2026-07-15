from __future__ import annotations

import pandas as pd
import streamlit as st

from .admin_service import request_full_analysis
from .agent_monitor import recent_agent_events, recent_agent_runs, recover_stale_events
from .system_overview import system_overview


def _event_table(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Событие": row["event_name"],
                "Состояние": row["status_name"],
                "Попыток": row["attempts"],
                "Ошибка": row["last_error"],
                "Время": row["updated_at"],
            }
            for row in rows
        ]
    )


def page_agent_center() -> None:
    st.header("Центр управления")
    values = system_overview()
    counts = values["agent"]["counts"]

    cols = st.columns(6)
    cols[0].metric("В яме", values["pit_observations"])
    cols[1].metric("Повторов", max(0, values["pit_occurrences"] - values["pit_observations"]))
    cols[2].metric("В базе", values["addresses"])
    cols[3].metric("На проверке", values["open_tasks"])
    cols[4].metric("Проверяют сейчас", values["active_leases"])
    cols[5].metric(
        "В очереди агента",
        int(counts.get("pending", 0)) + int(counts.get("retry", 0)),
    )

    worker = values["worker"]
    if worker["alive"]:
        st.success("Агент работает в фоне.")
    else:
        st.warning("Фоновый агент не запущен. Перезапустите приложение.")
    if worker.get("last_error"):
        st.error(worker["last_error"])

    daily = values.get("daily_audit")
    if daily:
        st.caption(f"Последний полный самоанализ: {daily['run_date']} · {daily['status']}")
    generation = values.get("latest_generation")
    if generation:
        stats = generation.get("stats") or {}
        st.caption(
            f"Последняя перестройка: проверено {stats.get('rows_scanned', 0)}, "
            f"изменено {stats.get('rows_changed', 0)}, "
            f"заданий {stats.get('tasks_created', 0)}."
        )

    left, middle, right = st.columns(3)
    if left.button("Полный самоанализ", type="primary", use_container_width=True):
        request_full_analysis(wait_for_agent=False)
        st.success("Самоанализ поставлен в очередь.")
        st.rerun()
    if middle.button("Восстановить зависшие", use_container_width=True):
        recovered = recover_stale_events()
        st.success(f"Возвращено в очередь: {recovered}.")
        st.rerun()
    if right.button("Обновить", use_container_width=True):
        st.rerun()

    events, runs = st.tabs(["События", "Запуски"])
    with events:
        rows = recent_agent_events(limit=100)
        if rows:
            st.dataframe(_event_table(rows), use_container_width=True, hide_index=True)
        else:
            st.info("Событий нет.")
    with runs:
        rows = recent_agent_runs(limit=100)
        if rows:
            frame = pd.DataFrame(
                [
                    {
                        "Событие": row["event_name"],
                        "Состояние": row["status_name"],
                        "Ошибка": row["error_text"],
                        "Начало": row["started_at"],
                        "Завершение": row["finished_at"],
                    }
                    for row in rows
                ]
            )
            st.dataframe(frame, use_container_width=True, hide_index=True)
        else:
            st.info("Запусков нет.")
