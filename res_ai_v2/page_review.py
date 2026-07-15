from __future__ import annotations

import streamlit as st

from .display_names import short_executor_name
from .review_experience import present_task
from .review_queue import claim_review_task, release_review_task
from .review_service import submit_review_and_update_agent
from .structure import CURRENT_STRUCTURE
from .ui_common import flash


def _skip_key(reviewer: str) -> str:
    return f"review_skipped::{reviewer.strip().lower()}"


def _option_label(item: dict[str, str]) -> str:
    branch = short_executor_name(item.get("branch", "").strip())
    res = short_executor_name(item.get("res", "").strip())
    return f"{res} — {branch}" if branch else res


def page_review(is_admin: bool, reviewer: str) -> None:
    st.header("Проверка")
    flash()
    if not reviewer.strip():
        st.info("Укажите фамилию и имя слева.")
        return

    skip_key = _skip_key(reviewer)
    skipped = set(st.session_state.get(skip_key, []))
    try:
        task = claim_review_task(reviewer, exclude_ids=skipped)
    except Exception as exc:
        st.error(str(exc))
        return

    if not task:
        if skipped:
            st.success("Других заданий сейчас нет.")
            if st.button("Вернуть пропущенные задания"):
                st.session_state[skip_key] = []
                st.rerun()
        else:
            st.success("Заданий нет.")
        return

    payload = dict(task.get("payload") or {})
    view = present_task(task)
    address = dict(payload.get("address") or {})
    st.subheader(view.question)
    if view.explanation:
        st.caption(view.explanation)
    st.info(view.address_line)

    if payload.get("query_text") or payload.get("raw_text"):
        with st.expander("Исходный текст"):
            st.write(payload.get("query_text") or payload.get("raw_text"))

    option_map = {_option_label(item): item["res"] for item in view.options}
    selected_res: list[str] = []
    if view.options:
        labels = list(option_map)
        if view.allow_multiple:
            selected_labels = st.multiselect(
                "Правильный РЭС",
                labels,
                placeholder="Выберите один или несколько РЭС",
            )
            selected_res.extend(option_map[label] for label in selected_labels)
        else:
            selected_label = st.radio(
                "Правильный РЭС",
                ["Не выбран"] + labels,
            )
            if selected_label != "Не выбран":
                selected_res.append(option_map[selected_label])

    with st.expander("Другой РЭС"):
        remaining = [res for res in CURRENT_STRUCTURE if res not in selected_res]
        other = st.selectbox(
            "РЭС",
            ["Не выбран"] + remaining,
            format_func=lambda value: value
            if value == "Не выбран"
            else short_executor_name(value),
        )
        if other != "Не выбран":
            selected_res.append(other)

    none_correct = st.checkbox("Правильного РЭС нет")
    locality = str(address.get("locality", ""))
    district = str(address.get("district", ""))
    settlement = str(address.get("settlement", ""))
    street = str(address.get("street", ""))
    if view.allow_address_edit:
        with st.expander("Исправить адрес"):
            cols = st.columns(2)
            locality = cols[0].text_input(
                "Населенный пункт",
                value=locality,
                key=f"loc_{task['id']}",
            )
            district = cols[1].text_input(
                "Район",
                value=district,
                key=f"dist_{task['id']}",
            )
            settlement = cols[0].text_input(
                "СНТ / поселок",
                value=settlement,
                key=f"set_{task['id']}",
            )
            street = cols[1].text_input(
                "Улица",
                value=street,
                key=f"str_{task['id']}",
            )

    if is_admin:
        with st.expander("Сведения"):
            st.write(f"Задание №{task['id']}")
            st.write(f"Тип: {task.get('task_type', '')}")

    left, right = st.columns([4, 1])
    if left.button("Подтвердить", type="primary", use_container_width=True):
        selected_res = list(dict.fromkeys(selected_res))
        if not selected_res and not none_correct:
            st.error("Выберите РЭС или отметьте, что правильного варианта нет.")
            return
        selection = {
            "selected_res": [] if none_correct else selected_res,
            "locality": locality,
            "district": district,
            "settlement": settlement,
            "street": street,
        }
        try:
            submit_review_and_update_agent(
                int(task["id"]),
                reviewer,
                selection,
                is_admin,
                str(task["lease_token"]),
                wait_for_agent=False,
            )
        except Exception as exc:
            st.error(str(exc))
            return
        st.session_state["flash"] = "Решение принято."
        st.rerun()

    if right.button("Пропустить", use_container_width=True):
        release_review_task(
            int(task["id"]),
            reviewer,
            str(task["lease_token"]),
        )
        skipped.add(int(task["id"]))
        st.session_state[skip_key] = sorted(skipped)
        st.rerun()
