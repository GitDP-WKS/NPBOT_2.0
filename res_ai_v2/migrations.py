from __future__ import annotations

from sqlalchemy import insert, select
from sqlalchemy.engine import Engine

from .db import utcnow
from .event_schema import agent_effects, agent_events, agent_runs  # noqa: F401
from .pit_schema import (  # noqa: F401
    agent_daily_runs,
    knowledge_directives,
    knowledge_generations,
    pit_observations,
    pit_occurrences,
    review_task_leases,
)
from .schema import metadata, schema_migrations

LATEST_SCHEMA_VERSION = 3


def run_migrations(engine: Engine) -> int:
    """Создает и версионирует таблицы РЭС AI.

    Старые таблицы не удаляются. Новые объекты добавляются только под префиксом
    res_ai_v2_. Вызов безопасно повторять при каждом запуске приложения.
    """
    metadata.create_all(engine)
    with engine.begin() as conn:
        applied = set(int(value) for value in conn.scalars(select(schema_migrations.c.version)))
        migrations = {
            1: "Начальная схема РЭС AI 2.0",
            2: "Событийная очередь и журнал запусков агента",
            3: "Сырая яма, директивы агента, поколения знаний и аренда заданий",
        }
        for version, description in migrations.items():
            if version in applied:
                continue
            conn.execute(
                insert(schema_migrations).values(
                    version=version,
                    description=description,
                    applied_at=utcnow(),
                )
            )
            applied.add(version)
    return max(applied) if applied else 0
