from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def temp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path}")
    monkeypatch.setenv("ADMIN_PASSWORD", "admin")
    from res_ai_v2 import db

    db.get_engine.cache_clear()
    db.initialize_database.cache_clear()
    db.initialize_database()
    yield path
    db.get_engine().dispose()
    db.get_engine.cache_clear()
    db.initialize_database.cache_clear()
