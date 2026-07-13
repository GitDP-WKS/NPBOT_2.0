from __future__ import annotations

from sqlalchemy import insert, select
from sqlalchemy.engine import Engine

from .db import utcnow
from .schema import metadata, schema_migrations

LATEST_SCHEMA_VERSION = 1


def run_migrations(engine: Engine) -> int:
    """Простая версионная схема миграций для собственных таблиц res_ai_v2_*.

    Старые таблицы РЭС AI 1 не изменяются. Новые миграции добавляются отдельными
    функциями и фиксируются в res_ai_v2_schema_migrations.
    """
    metadata.create_all(engine)
    with engine.begin() as conn:
        applied = set(int(value) for value in conn.scalars(select(schema_migrations.c.version)))
        if 1 not in applied:
            conn.execute(
                insert(schema_migrations).values(
                    version=1,
                    description="Начальная схема РЭС AI 2.0",
                    applied_at=utcnow(),
                )
            )
            applied.add(1)
    return max(applied) if applied else 0
