from __future__ import annotations

from pathlib import Path


def test_admin_login_supports_enter_submission() -> None:
    source = Path("res_ai_v2/ui_common.py").read_text(encoding="utf-8")

    assert 'st.form("admin_login_form")' in source
    assert "st.form_submit_button" in source


def test_admin_login_opens_lightweight_page_before_rerun() -> None:
    source = Path("res_ai_v2/ui_common.py").read_text(encoding="utf-8")

    navigation = 'st.session_state["main_navigation"] = "Загрузка"'
    rerun = "st.rerun()"
    success = 'st.session_state["is_admin"] = True'

    success_pos = source.index(success)
    navigation_pos = source.index(navigation, success_pos)
    rerun_pos = source.index(rerun, navigation_pos)

    assert success_pos < navigation_pos < rerun_pos
