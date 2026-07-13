from __future__ import annotations

import streamlit as st

from .repositories import list_review_tasks
from .reviews import submit_review
from .structure import CURRENT_STRUCTURE
from .ui_common import display_options, flash


def _queue_key(reviewer: str) -> str: return f"v2_queue::{reviewer.strip().lower() or 'anonymous'}"


def _load(reviewer: str, force: bool = False):
    key = _queue_key(reviewer)
    if force or key not in st.session_state: st.session_state[key] = list_review_tasks(reviewer, 50)
    return st.session_state[key]


def page_review(is_admin: bool, reviewer: str) -> None:
    st.header("Проверка")
    flash()
    if not reviewer.strip(): st.info("Укажите фамилию и имя проверяющего слева."); return
    queue = _load(reviewer)
    if not queue: queue = _load(reviewer, True)
    if not queue: st.success("Сомнений, требующих проверки, сейчас нет."); return
    task = queue[0]; payload = task["payload"]; options = payload.get("options", [])
    st.caption(f"{task['title']} · в локальной очереди осталось: {len(queue)}")
    if payload.get("query_text"): st.info(payload["query_text"])
    address = payload.get("address", {})
    display_options(options)
    labels = [f"{row.get('branch','')} · {row.get('res','')}" for row in options if row.get("res")]
    map_res = {f"{row.get('branch','')} · {row.get('res','')}": row.get("res", "") for row in options if row.get("res")}
    selected_labels = st.multiselect("Правильные варианты", labels, default=[])
    selected_res = [map_res[label] for label in selected_labels]
    remaining = [res for res in CURRENT_STRUCTURE if res not in selected_res]
    other = st.selectbox("Другой официальный РЭС", ["Не выбран"] + remaining)
    if other != "Не выбран": selected_res.append(other)
    none_correct = st.checkbox("Подходящего РЭС нет")
    st.subheader("Адресные данные")
    cols = st.columns(4)
    locality = cols[0].text_input("Населенный пункт", value=str(address.get("locality", "")), key=f"loc_{task['id']}")
    district = cols[1].text_input("Район", value=str(address.get("district", "")), key=f"dist_{task['id']}")
    settlement = cols[2].text_input("СНТ / поселок", value=str(address.get("settlement", "")), key=f"set_{task['id']}")
    street = cols[3].text_input("Улица", value=str(address.get("street", "")), key=f"str_{task['id']}")
    left, right = st.columns([3,1])
    if left.button("Подтвердить и открыть следующее", type="primary", use_container_width=True):
        if not selected_res and not none_correct: st.error("Выберите РЭС или отметьте, что подходящего нет."); return
        selection = {"selected_res": [] if none_correct else list(dict.fromkeys(selected_res)), "locality": locality, "district": district, "settlement": settlement, "street": street}
        try: result = submit_review(int(task["id"]), reviewer, selection, is_admin)
        except Exception as exc: st.error(str(exc)); return
        queue.pop(0)
        st.session_state["flash"] = "Решение применено." if result.get("applied") else f"Голос сохранен: {result.get('votes',0)} из {result.get('required',3)}."
        st.rerun()
    if right.button("Пропустить", use_container_width=True): queue.append(queue.pop(0)); st.rerun()
