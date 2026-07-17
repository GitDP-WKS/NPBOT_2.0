from pathlib import Path


def test_streamlit_code_uses_current_width_api() -> None:
    offenders: list[str] = []
    for path in sorted(Path("res_ai_v2").rglob("*.py")):
        if "use_container_width" in path.read_text(encoding="utf-8"):
            offenders.append(path.as_posix())
    assert not offenders, f"Устаревший use_container_width найден в: {', '.join(offenders)}"
