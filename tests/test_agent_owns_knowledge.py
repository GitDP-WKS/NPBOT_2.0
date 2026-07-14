from __future__ import annotations

import ast
import re
from pathlib import Path

PROTECTED_TABLES = {
    "addresses",
    "address_mappings",
    "address_aliases",
    "mapping_evidence",
    "text_examples",
    "conditional_rules",
    "mapping_explanations",
}
ALLOWED_AGENT_WRITERS = {
    "res_ai_v2/knowledge_writer.py",
    "res_ai_v2/knowledge_writer_v3.py",
    "res_ai_v2/domain_writer.py",
    "res_ai_v2/review_helpers.py",
}
WRITE_FUNCTIONS = {"insert", "update", "delete"}
RAW_SQL_WRITE = re.compile(r"\b(insert\s+into|update|delete\s+from)\s+([a-zA-Z0-9_]+)", re.I)


def _name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _table_from_expression(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


class WorkingKnowledgeWriteVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.violations: list[tuple[int, str]] = []

    def visit_Call(self, node: ast.Call) -> None:
        function_name = _name(node.func)
        if function_name in WRITE_FUNCTIONS and node.args:
            table_name = _table_from_expression(node.args[0])
            if table_name in PROTECTED_TABLES:
                self.violations.append(
                    (node.lineno, f"{function_name}({table_name})")
                )

        if isinstance(node.func, ast.Attribute) and node.func.attr in WRITE_FUNCTIONS:
            table_name = _table_from_expression(node.func.value)
            if table_name in PROTECTED_TABLES:
                self.violations.append(
                    (node.lineno, f"{table_name}.{node.func.attr}()")
                )

        if function_name == "text" and node.args:
            value = node.args[0]
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                for match in RAW_SQL_WRITE.finditer(value.value):
                    table = match.group(2).removeprefix("res_ai_v2_")
                    if table in PROTECTED_TABLES:
                        self.violations.append(
                            (node.lineno, f"raw SQL write to {table}")
                        )
        self.generic_visit(node)


def _python_files(root: Path) -> list[Path]:
    return sorted(
        [*root.joinpath("res_ai_v2").rglob("*.py"), *root.joinpath("scripts").rglob("*.py")]
    )


def test_only_agent_writer_layer_changes_working_knowledge() -> None:
    root = Path(__file__).resolve().parents[1]
    violations: list[str] = []
    for path in _python_files(root):
        relative = path.relative_to(root).as_posix()
        if relative in ALLOWED_AGENT_WRITERS:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
        visitor = WorkingKnowledgeWriteVisitor()
        visitor.visit(tree)
        violations.extend(
            f"{relative}:{line}: {description}"
            for line, description in visitor.violations
        )
    assert not violations, "Прямая запись вне слоя агента:\n" + "\n".join(violations)


def test_forbidden_application_layers_are_not_agent_writers() -> None:
    forbidden_parts = {
        "api",
        "page_",
        "ui",
        "import",
        "review_service",
        "reviews",
        "daily",
        "incremental",
        "legacy",
        "backup",
    }
    assert not {
        path
        for path in ALLOWED_AGENT_WRITERS
        if any(part in path for part in forbidden_parts)
    }
