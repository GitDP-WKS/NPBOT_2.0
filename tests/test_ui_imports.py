from __future__ import annotations


def test_streamlit_entrypoint_imports_all_pages() -> None:
    from res_ai_v2 import ui
    from res_ai_v2.page_data_admin import page_settings

    assert callable(ui.main)
    assert callable(page_settings)
