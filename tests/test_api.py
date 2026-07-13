from __future__ import annotations

from res_ai_v2.api import health
from res_ai_v2.db import initialize_database


def test_health_endpoint_initializes_database(temp_db) -> None:
    initialize_database()
    assert health() == {"status": "ok"}
