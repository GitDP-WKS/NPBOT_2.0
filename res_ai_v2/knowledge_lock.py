from __future__ import annotations

import threading
from contextlib import contextmanager
from collections.abc import Iterator

from sqlalchemy import text

from .db import get_engine

# Постоянный идентификатор блокировки только для РЭС AI.
POSTGRES_LOCK_ID = 734_202_603
_LOCAL_LOCK = threading.RLock()


@contextmanager
def knowledge_write_lock() -> Iterator[None]:
    """Разрешает только одну перестройку рабочей базы во всех процессах."""
    engine = get_engine()
    if engine.dialect.name != "postgresql":
        with _LOCAL_LOCK:
            yield
        return

    connection = engine.connect()
    try:
        connection.execute(
            text("SELECT pg_advisory_lock(:lock_id)"),
            {"lock_id": POSTGRES_LOCK_ID},
        )
        yield
    finally:
        try:
            connection.execute(
                text("SELECT pg_advisory_unlock(:lock_id)"),
                {"lock_id": POSTGRES_LOCK_ID},
            )
        finally:
            connection.close()
