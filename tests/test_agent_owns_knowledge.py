from __future__ import annotations

from pathlib import Path


WRITES = (
    "insert(addresses)",
    "update(addresses)",
    "delete(addresses)",
    "insert(address_mappings)",
    "update(address_mappings)",
    "delete(address_mappings)",
    "insert(mapping_evidence)",
    "update(mapping_evidence)",
    "delete(mapping_evidence)",
    "insert(text_examples)",
    "update(text_examples)",
    "delete(text_examples)",
    "insert(address_aliases)",
    "update(address_aliases)",
    "delete(address_aliases)",
)

ALLOWED_WRITERS = {
    "knowledge_agent.py",
    "review_helpers.py",
    "pit_bootstrap.py",
    "legacy_migration.py",
}


def test_only_agent_modules_write_working_knowledge() -> None:
    root = Path(__file__).resolve().parents[1] / "res_ai_v2"
    violations = []
    for path in root.glob("*.py"):
        if path.name in ALLOWED_WRITERS:
            continue
        text = path.read_text(encoding="utf-8")
        markers = [marker for marker in WRITES if marker in text]
        if markers:
            violations.append(f"{path.name}: {', '.join(markers)}")
    assert not violations, "Прямая запись в базу знаний: " + "; ".join(violations)
