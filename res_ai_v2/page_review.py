from __future__ import annotations

from uuid import uuid4

import streamlit as st

from .display_names import short_executor_name
from .review_experience import present_task
from .review_queue import claim_review_task, release_review_task
from .review_service import submit_review_and_update_agent
from .structure import CURRENT_STRUCTURE
from .ui_common import flash


def _skip_key(reviewer: str) -> str:
    return f"review_skipped::{reviewer.strip().lower()}"


def _lease_owner(reviewer: str) -> str:
    if "review_session_id" not in st.session_state:
        st.session_state["review_session_id"] = uuid4().hex
    return f"{reviewer}::{st.session_state['review_session_id']}"


def _option_label(item: dict[str, str]) -> str:
    branch = short_executor_name(item.get("branch", "").strip())
    res = short_executor_name(item.get("res", "").strip())
    return f"{res} — {branch}" if branch else res


def _submit(
    task: dict,
    reviewer: str,
    lease_owner: str,
    selection: dict,
    is_admin: bool,
) -> None:
    try:
        submit_review_and_update_agent(
            int(task["id"]),
            reviewer,
            selection,
            is_admin,
            str(task["lease_token"]),
            lease_owner=lease_owner,
            wait_for_agent=False,
        )
    except ValueError as exc:
        message = str(exc)
        if "уже проверяли" in message.lower() or "уже закрыто" in message.lower():
            release_review_task(int(task["id"]), lease_owner, str(task["lease_token"]))
            st.session_state["flash"] = "Задание уже обработано. Показываю следующее."
            st.rerun()
        st.error(message)
        return
    except Exception as exc:
        st.error(str(exc))
        return
    st.session_state.pop(f"review_edit::{task['id']}", None)
    st.session_state["flash"] = "Решение принято."
    st.rerun()


def page_review(is_admin: bool, reviewer: str) -> None:
    st.header("Проверка")
    flash()
    if not reviewer.strip():
        st.info("Укажите фамилию и имя слева.")
        return

    skip_key = _skip_key(reviewer)
    skipped = set(st.session_state.get(skip_key, []))
    owner = _lease_owner(reviewer)
    try:
        task = claim_review_task(reviewer, lease_owner=owner, exclude_ids=skipped)
    except Exception as exc:
        st.error(str(exc))
        return

    if not task:
        if skipped:
            st.success("Других заданий сейчас нет.")
            if st.button("Вернуть пропущенные", use_container_width=True):
                st.session_state[skip_key] = []
                st.rerun()
        else:
            st.success("Заданий нет.")
        return

    payload = dict(task.get("payload") or {})
    view = present_task(task)
    address = dict(payload.get("address") or {})
    options = list(view.options)
    proposed = options[0] if options else None
    proposed_res = str(proposed.get("res", "")) if proposed else ""
    proposed_branch = str(proposed.get("branch", "")) if proposed else ""
    confidence = payload.get("confidence", payload.get("score"))

    st.markdown(
        f"""
        <div style="border:1px solid rgba(128,128,128,.35);border-radius:18px;padding:22px;margin-bottom:16px">
          <div style="font-size:1rem;font-weight:650;margin-bottom:8px">Исходный адрес</div>
          <div style="font-size:1.15rem">{view.address_line}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.subheader("Ответ агента")
    cols = st.columns(3)
    cols[0].metric("Филиал", short_executor_name(proposed_branch) if proposed_branch else "Не определен")
    cols[1].metric("РЭС", short_executor_name(proposed_res) if proposed_res else "Не определен")
    cols[2].metric("Точность", f"{float(confidence):.1f}%" if confidence not in (None, "") else "—")
    if view.explanation:
        st.caption(view.explanation)

    confirm, correct, insufficient = st.columns(3)
    if confirm.button(
        "Подтвердить",
        type="primary",
        use_container_width=True,
        disabled=not bool(proposed_res),
    ):
        _submit(
            task,
            reviewer,
            owner,
            {
                "selected_res": [proposed_res],
                "decision_type": "confirmed",
                "locality": str(address.get("locality", "")),
                "district": str(address.get("district", "")),
                "settlement": str(address.get("settlement", "")),
                "street": str(address.get("street", "")),
            },
            is_admin,
        )

    edit_key = f"review_edit::{task['id']}"
    if correct.button("Исправить", use_container_width=True):
        st.session_state[edit_key] = not bool(st.session_state.get(edit_key))
        st.rerun()

    if insufficient.button("Недостаточно данных", use_container_width=True):
        _submit(
            task,
            reviewer,
            owner,
            {
                "selected_res": [],
                "decision_type": "insufficient_data",
                "locality": str(address.get("locality", "")),
                "district": str(address.get("district", "")),
                "settlement": str(address.get("settlement", "")),
                "street": str(address.get("street", "")),
            },
            is_admin,
        )

    if st.session_state.get(edit_key):
        st.divider()
        st.subheader("Исправление ответа")
        selected = st.selectbox(
            "Правильный РЭС",
            list(CURRENT_STRUCTURE),
            index=None,
            placeholder="Выберите РЭС",
            format_func=short_executor_name,
            key=f"correct_res::{task['id']}",
        )
        branch = CURRENT_STRUCTURE.get(selected, "") if selected else ""
        st.text_input(
            "Филиал",
            value=short_executor_name(branch),
            disabled=True,
            key=f"correct_branch::{task['id']}",
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
                "СНТ / поселок",
                value=str(address.get("settlement", "")),
                key=f"set_{task['id']}",
            )
            street = right.text_input(
                "Улица",
                value=str(address.get("street", "")),
                key=f"str_{task['id']}",
            )
        save, cancel = st.columns(2)
        if save.button(
            "Сохранить исправление",
            type="primary",
            use_container_width=True,
            disabled=not bool(selected),
        ):
            _submit(
                task,
                reviewer,
                owner,
                {
                    "selected_res": [selected],
                    "decision_type": "selected_other",
                    "locality": locality,
                    "district": district,
                    "settlement": settlement,
                    "street": street,
                },
                is_admin,
            )
        if cancel.button("Отмена", use_container_width=True):
            st.session_state[edit_key] = False
            st.rerun()

    footer_left, footer_right = st.columns([4, 1])
    if is_admin:
        footer_left.caption(f"Задание №{task['id']} · {task.get('task_type', '')}")
    if footer_right.button("Пропустить", use_container_width=True):
        release_review_task(int(task["id"]), owner, str(task["lease_token"]))
        skipped.add(int(task["id"]))
        st.session_state[skip_key] = sorted(skipped)
        st.rerun()
