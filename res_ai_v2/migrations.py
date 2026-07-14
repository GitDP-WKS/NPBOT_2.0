from __future__ import annotations

from sqlalchemy import insert, select
from sqlalchemy.engine import Engine

from .db import utcnow
from .event_schema import agent_effects, agent_events, agent_runs  # noqa: F401
from .schema import metadata, schema_migrations

LATEST_SCHEMA_VERSION = 2


def run_migrations(engine: Engine) -> int:
    """Создает и версионирует собственные таблицы РЭС AI 2.0.

    Старые таблицы не изменяются. Новые объекты добавляются только под префиксом
    res_ai_v2_. Вызов безопасно повторять при каждом запуске приложения.
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
        if 2 not in applied:
            conn.execute(
                insert(schema_migrations).values(
                    version=2,
                    description="Событийная очередь и журнал запусков агента",
                    applied_at=utcnow(),
                )
            )
            applied.add(2)
    return max(applied) if applied else 0
