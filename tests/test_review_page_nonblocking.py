from __future__ import annotations

import ast
from pathlib import Path


def test_review_page_does_not_claim_task_during_render() -> None:
    source = Path("res_ai_v2/page_review.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    page_review = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "page_review"
    )
    direct_claims = [
        node
        for node in ast.walk(page_review)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "claim_review_task"
    ]
    assert direct_claims == []


def test_review_task_is_loaded_only_by_explicit_loader() -> None:
    source = Path("res_ai_v2/page_review.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    loader = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_load_task"
    )
    calls = [
        node
        for node in ast.walk(loader)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "claim_review_task"
    ]
    assert len(calls) == 1
