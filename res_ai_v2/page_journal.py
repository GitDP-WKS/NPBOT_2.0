from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from .repositories import recent_audit
from .reviews import recent_decisions, recent_votes, undo_decision
from .ui_labels import action_label, entity_label, status_label, task_type_label


def _selection_text(value: object) -> str:
    try:
        payload = json.loads(str(value) or "{}")
    except json.JSONDecodeError:
        return "Не удалось прочитать"
    selected = payload.get("selected_res", []) if isinstance(payload, dict) else []
    return ", ".join(str(item) for item in selected) or "Не выбран"


def page_journal() -> None:
    st.header("Журнал")
    top_left, top_right = st.columns([3, 1])
    top_left.caption("Данные читаются из общей базы Neon. Здесь видны действия со всех компьютеров.")
    if top_right.button("Обновить данные", use_container_width=True):
        st.rerun()

    tab1, tab2, tab3 = st.tabs(["Голоса проверяющих", "Принятые решения", "Все действия"])

    with tab1:
        votes = recent_votes()
        if not votes:
            st.info("Голосов проверяющих пока нет.")
        else:
            frame = pd.DataFrame(votes)
            frame["task_type"] = frame["task_type"].map(task_type_label)
            frame["task_status"] = frame["task_status"].map(status_label)
            frame["is_admin"] = frame["is_admin"].map(lambda value: "Администратор" if value else "Проверяющий")
            rename = {
                "reviewer": "Пользователь",
                "is_admin": "Роль",
                "task_id": "Номер задания",
                "title": "Задание",
                "task_type": "Тип задания",
                "selected_res": "Выбранный РЭС",
                "address_changes": "Внесенные данные",
                "vote_progress": "Ход подтверждения",
                "result": "Результат",
                "created_at": "Дата и время",
            }
            visible = [column for column in rename if column in frame.columns]
            st.dataframe(
                frame[visible].rename(columns=rename),
                use_container_width=True,
                hide_index=True,
            )

    with tab2:
        decisions = recent_decisions()
        if not decisions:
            st.info("Принятых решений пока нет.")
        else:
            frame = pd.DataFrame(decisions)
            frame["task_type"] = frame["task_type"].map(task_type_label)
            frame["selection_json"] = frame["selection_json"].map(_selection_text)
            frame["active"] = frame["active"].map(lambda value: "Действует" if value else "Отменено")
            rename = {
                "id": "Номер решения",
                "task_id": "Номер задания",
                "applied_by": "Кем применено",
                "title": "Задание",
                "task_type": "Тип задания",
                "selection_json": "Выбранный РЭС",
                "active": "Состояние",
                "created_at": "Дата и время",
            }
            visible = [column for column in rename if column in frame.columns]
            st.dataframe(
                frame[visible].rename(columns=rename),
                use_container_width=True,
                hide_index=True,
            )
            active = [row for row in decisions if row.get("active")]
            if active:
                labels = {
                    f"№{row['id']} · {row['title']} · {row['applied_by']}": int(row["id"])
                    for row in active
                }
                selected = st.selectbox("Решение для отмены", list(labels))
                if st.button("Отменить выбранное решение"):
                    try:
                        undo_decision(labels[selected])
                        st.success("Решение отменено, задание снова открыто.")
                        st.rerun()
                    except Exception as exc:
                        st.error(str(exc))

    with tab3:
        rows = recent_audit()
        if not rows:
            st.info("Действий пока нет.")
        else:
            frame = pd.DataFrame(rows)
            rename = {
                "actor": "Пользователь",
                "action": "Действие",
                "entity_type": "Объект",
                "entity_key": "Идентификатор",
                "created_at": "Дата и время",
            }
            frame = frame.rename(columns=rename)
            if "Действие" in frame:
                frame["Действие"] = frame["Действие"].map(action_label)
            if "Объект" in frame:
                frame["Объект"] = frame["Объект"].map(entity_label)
            visible = [column for column in rename.values() if column in frame.columns]
            st.dataframe(frame[visible], use_container_width=True, hide_index=True)
