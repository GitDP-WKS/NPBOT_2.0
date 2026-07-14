from __future__ import annotations

import streamlit as st

from .repositories import list_review_tasks
from .review_experience import present_task
from .reviews import submit_review
from .structure import CURRENT_STRUCTURE
from .ui_common import flash


def _queue_key(reviewer: str) -> str:
    return f"v2_queue::{reviewer.strip().lower() or 'anonymous'}"


def _load(reviewer: str, force: bool = False):
    key = _queue_key(reviewer)
    if force or key not in st.session_state:
        st.session_state[key] = list_review_tasks(reviewer, 50)
    return st.session_state[key]


def _option_label(item: dict[str, str]) -> str:
    branch = item.get("branch", "").strip()
    res = item.get("res", "").strip()
    return f"{res} — {branch}" if branch else res


def page_review(is_admin: bool, reviewer: str) -> None:
    st.header("Проверка")
    flash()

    if not reviewer.strip():
        st.info("Укажите фамилию и имя слева, чтобы начать проверку.")
        return

    queue = _load(reviewer)
    if not queue:
        queue = _load(reviewer, True)
    if not queue:
        st.success("Сейчас нет записей, где требуется решение человека.")
        return

    task = queue[0]
    payload = dict(task.get("payload") or {})
    view = present_task(task)
    address = dict(payload.get("address") or {})

    st.subheader(view.question)
    st.caption(view.explanation)
    st.info(view.address_line)

    if payload.get("query_text"):
        with st.expander("Показать исходный текст"):
            st.write(payload["query_text"])

    option_map = {_option_label(item): item["res"] for item in view.options}
    selected_res: list[str] = []

    if view.options:
        st.markdown("**Выберите правильный РЭС**")
        labels = list(option_map)
        if view.allow_multiple:
            selected_labels = st.multiselect(
                "Можно выбрать несколько вариантов",
                labels,
                label_visibility="collapsed",
                placeholder="Нажмите и выберите один или несколько РЭС",
            )
            selected_res.extend(option_map[label] for label in selected_labels)
        else:
            selected_label = st.radio(
                "Правильный вариант",
                ["Пока не выбран"] + labels,
                label_visibility="collapsed",
            )
            if selected_label != "Пока не выбран":
                selected_res.append(option_map[selected_label])
    else:
        st.warning("Система не смогла предложить подходящий вариант.")

    with st.expander("Выбрать другой РЭС"):
        remaining = [res for res in CURRENT_STRUCTURE if res not in selected_res]
        other = st.selectbox("Другой официальный РЭС", ["Не выбран"] + remaining)
        if other != "Не выбран":
            selected_res.append(other)

    none_correct = st.checkbox("Правильного РЭС нет в списке")

    locality = str(address.get("locality", ""))
    district = str(address.get("district", ""))
    settlement = str(address.get("settlement", ""))
    street = str(address.get("street", ""))

    if view.allow_address_edit:
        with st.expander("Исправить или дополнить адрес"):
            cols = st.columns(2)
            locality = cols[0].text_input("Населенный пункт", value=locality, key=f"loc_{task['id']}")
            district = cols[1].text_input("Район", value=district, key=f"dist_{task['id']}")
            settlement = cols[0].text_input("СНТ / поселок", value=settlement, key=f"set_{task['id']}")
            street = cols[1].text_input("Улица", value=street, key=f"str_{task['id']}")

    if is_admin:
        with st.expander("Служебные сведения"):
            st.write(f"Номер задания: {task['id']}")
            st.write(f"Тип задания: {task.get('task_type', '')}")
            st.write(f"Приоритет: {task.get('priority', 0)}")

    left, right = st.columns([4, 1])
    if left.button("Сохранить решение и открыть следующее", type="primary", use_container_width=True):
        selected_res = list(dict.fromkeys(selected_res))
        if not selected_res and not none_correct:
            st.error("Выберите правильный РЭС или отметьте, что подходящего варианта нет.")
            return

        selection = {
            "selected_res": [] if none_correct else selected_res,
            "locality": locality,
            "district": district,
            "settlement": settlement,
            "street": street,
        }
        try:
            result = submit_review(int(task["id"]), reviewer, selection, is_admin)
        except Exception as exc:
            st.error(str(exc))
            return

        queue.pop(0)
        st.session_state["flash"] = (
            "Решение применено к базе знаний."
            if result.get("applied")
            else f"Ответ сохранен. Совпадающих решений: {result.get('votes', 0)} из {result.get('required', 3)}."
        )
        st.rerun()

    if right.button("Пропустить", use_container_width=True):
        queue.append(queue.pop(0))
        st.rerun()
