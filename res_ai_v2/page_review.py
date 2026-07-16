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


def _edit_key(task_id: int) -> str:
    return f"review_edit::{task_id}"


def _option_label(item: dict[str, str]) -> str:
    branch = short_executor_name(item.get("branch", "").strip())
    res = short_executor_name(item.get("res", "").strip())
    return f"{res} — {branch}" if branch else res


def _submit(
    task: dict,
    reviewer: str,
    is_admin: bool,
    selection: dict,
    message: str,
) -> None:
    submit_review_and_update_agent(
        int(task["id"]),
        reviewer,
        selection,
        is_admin,
        str(task["lease_token"]),
        wait_for_agent=False,
    )
    st.session_state.pop(_edit_key(int(task["id"])), None)
    st.session_state["flash"] = message
    st.rerun()


def _agent_answer(view, payload: dict) -> tuple[str, str, float]:
    options = list(view.options)
    first = options[0] if options else {}
    branch = str(first.get("branch", "")).strip()
    res = str(first.get("res", "")).strip()
    confidence = float(payload.get("confidence", payload.get("score", 0)) or 0)
    if confidence <= 1:
        confidence *= 100
    return branch, res, confidence


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
        st.success("Заданий нет.")
        if skipped and st.button("Вернуть пропущенные", use_container_width=True):
            st.session_state[skip_key] = []
            st.rerun()
        return

    payload = dict(task.get("payload") or {})
    view = present_task(task)
    address = dict(payload.get("address") or {})
    branch, proposed_res, confidence = _agent_answer(view, payload)

    with st.container(border=True):
        st.subheader("Исходный адрес")
        st.info(view.address_line)
        raw_text = str(payload.get("query_text") or payload.get("raw_text") or "").strip()
        if raw_text and raw_text != view.address_line:
            st.caption(raw_text)

    st.subheader("Ответ агента")
    answer_cols = st.columns(3)
    answer_cols[0].metric("Филиал", short_executor_name(branch) if branch else "Не определен")
    answer_cols[1].metric("РЭС", short_executor_name(proposed_res) if proposed_res else "Не определен")
    answer_cols[2].metric("Точность", f"{confidence:.0f}%" if confidence else "—")
    if view.explanation:
        st.caption(view.explanation)

    confirm_col, correct_col, insufficient_col = st.columns(3)
    if confirm_col.button(
        "Подтвердить",
        type="primary",
        use_container_width=True,
        disabled=not bool(proposed_res),
    ):
        _submit(
            task,
            reviewer,
            is_admin,
            {
                "decision_type": "confirmed",
                "selected_res": [proposed_res],
                "locality": str(address.get("locality", "")),
                "district": str(address.get("district", "")),
                "settlement": str(address.get("settlement", "")),
                "street": str(address.get("street", "")),
            },
            "Ответ подтвержден. Открыто следующее задание.",
        )

    if correct_col.button("Исправить", use_container_width=True):
        st.session_state[_edit_key(int(task["id"]))] = True
        st.rerun()

    if insufficient_col.button("Недостаточно данных", use_container_width=True):
        _submit(
            task,
            reviewer,
            is_admin,
            {"decision_type": "insufficient_data", "selected_res": []},
            "Задание отмечено как неполное. Открыто следующее задание.",
        )

    if st.session_state.get(_edit_key(int(task["id"]))):
        with st.container(border=True):
            st.subheader("Исправление ответа")
            selected_res = st.selectbox(
                "Правильный РЭС",
                [""] + list(CURRENT_STRUCTURE),
                format_func=lambda value: "Выберите РЭС"
                if not value
                else short_executor_name(value),
                key=f"correct_res_{task['id']}",
            )
            selected_branch = CURRENT_STRUCTURE.get(selected_res, "") if selected_res else ""
            st.text_input(
                "Филиал",
                value=short_executor_name(selected_branch),
                disabled=True,
                key=f"correct_branch_{task['id']}",
            )

            with st.expander("Исправить адрес"):
                left, right = st.columns(2)
                locality = left.text_input(
                    "Населенный пункт",
                    value=str(address.get("locality", "")),
                    key=f"loc_{task['id']}",
                )
                district = right.text_input(
                    "Район",
                    value=str(address.get("district", "")),
                    key=f"dist_{task['id']}",
                )
                settlement = left.text_input(
                    "СНТ / территория",
                    value=str(address.get("settlement", "")),
                    key=f"set_{task['id']}",
                )
                street = right.text_input(
                    "Улица",
                    value=str(address.get("street", "")),
                    key=f"str_{task['id']}",
                )

            save_col, cancel_col = st.columns(2)
            if save_col.button(
                "Сохранить исправление",
                type="primary",
                use_container_width=True,
                disabled=not bool(selected_res),
            ):
                _submit(
                    task,
                    reviewer,
                    is_admin,
                    {
                        "decision_type": "selected_other",
                        "selected_res": [selected_res],
                        "locality": locality,
                        "district": district,
                        "settlement": settlement,
                        "street": street,
                    },
                    "Исправление сохранено. Открыто следующее задание.",
                )
            if cancel_col.button("Отмена", use_container_width=True):
                st.session_state.pop(_edit_key(int(task["id"])), None)
                st.rerun()

    footer_left, footer_right = st.columns([4, 1])
    if footer_left.button("Ошибка источника", use_container_width=True):
        _submit(
            task,
            reviewer,
            is_admin,
            {"decision_type": "source_error", "selected_res": []},
            "Ошибка источника отмечена. Открыто следующее задание.",
        )
    if footer_right.button("Пропустить", use_container_width=True):
        release_review_task(
            int(task["id"]),
            reviewer,
            str(task["lease_token"]),
        )
        skipped.add(int(task["id"]))
        st.session_state[skip_key] = sorted(skipped)
        st.rerun()

    if is_admin:
        with st.expander("Сведения"):
            st.write(f"Задание №{task['id']}")
            st.write(f"Тип: {task.get('task_type', '')}")
