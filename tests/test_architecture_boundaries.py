from __future__ import annotations

from pathlib import Path


FORBIDDEN_PAGE_IMPORTS = (
    "from .analyzer import",
    "from .incremental_analyzer import",
    "from .model_training import",
    "from .reviews import submit_review",
    "from .modeling import train_candidate",
)


def test_streamlit_pages_do_not_call_low_level_agent_logic_directly() -> None:
    root = Path(__file__).resolve().parents[1] / "res_ai_v2"
    violations: list[str] = []
    for path in sorted(root.glob("page_*.py")):
        text = path.read_text(encoding="utf-8")
        for forbidden in FORBIDDEN_PAGE_IMPORTS:
            if forbidden in text:
                violations.append(f"{path.name}: {forbidden}")
    assert not violations, "Интерфейс обходит прикладные службы: " + "; ".join(violations)


def test_entrypoint_contains_no_business_logic() -> None:
    root = Path(__file__).resolve().parents[1]
    text = (root / "app.py").read_text(encoding="utf-8")
    assert "res_ai_v2.ui" in text
    assert "sqlalchemy" not in text
    assert "streamlit" not in text
