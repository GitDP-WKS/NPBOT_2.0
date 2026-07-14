from __future__ import annotations

from res_ai_v2.api import health, system_status
from res_ai_v2.db import initialize_database


def test_health_endpoint_initializes_database(temp_db) -> None:
    initialize_database()
    assert health() == {"status": "ok", "storage": "SQLite (локально)"}


def test_system_status_contains_agent_queue(temp_db) -> None:
    result = system_status()
    assert result["storage"] == "SQLite (локально)"
    assert "database" in result
    assert "event_queue" in result
    assert "diagnostics" in result
    assert result["diagnostics"]["healthy"] is True
