from __future__ import annotations

from datetime import UTC, datetime
from functools import lru_cache
from typing import Any

from sqlalchemy import create_engine, insert, select, update
from sqlalchemy.engine import Engine

from .config import load_settings
from .schema import executor_structure, settings
from .structure import CURRENT_STRUCTURE


def utcnow() -> datetime:
    return datetime.now(UTC)


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    cfg = load_settings()
    kwargs: dict[str, Any] = {
        "pool_pre_ping": True,
        "pool_recycle": 240,
        "future": True,
    }
    if cfg.database_url.startswith("postgresql"):
        kwargs["connect_args"] = {"connect_timeout": 15}
    engine = create_engine(cfg.database_url, **kwargs)
    return engine


@lru_cache(maxsize=1)
def initialize_database() -> None:
    engine = get_engine()
    from .migrations import run_migrations

    run_migrations(engine)
    now = utcnow()
    with engine.begin() as conn:
        existing = set(conn.scalars(select(executor_structure.c.res_name)))
        for res_name, branch_name in CURRENT_STRUCTURE.items():
            if res_name in existing:
                conn.execute(
                    update(executor_structure)
                    .where(executor_structure.c.res_name == res_name)
                    .values(branch_name=branch_name, active=True, updated_at=now)
                )
            else:
                conn.execute(
                    insert(executor_structure).values(
                        res_name=res_name,
                        branch_name=branch_name,
                        active=True,
                        created_at=now,
                        updated_at=now,
                    )
                )
        if not conn.execute(select(settings.c.key).where(settings.c.key == "data_version")).first():
            conn.execute(insert(settings).values(key="data_version", value="1", updated_at=now))
        if not conn.execute(select(settings.c.key).where(settings.c.key == "human_decisions_since_training")).first():
            conn.execute(
                insert(settings).values(
                    key="human_decisions_since_training",
                    value="0",
                    updated_at=now,
                )
            )


def get_setting(key: str, default: str = "") -> str:
    initialize_database()
    with get_engine().connect() as conn:
        value = conn.scalar(select(settings.c.value).where(settings.c.key == key))
    return default if value is None else str(value)


def set_setting(key: str, value: Any) -> None:
    initialize_database()
    now = utcnow()
    with get_engine().begin() as conn:
        if conn.execute(select(settings.c.key).where(settings.c.key == key)).first():
            conn.execute(update(settings).where(settings.c.key == key).values(value=str(value), updated_at=now))
        else:
            conn.execute(insert(settings).values(key=key, value=str(value), updated_at=now))


def bump_data_version() -> int:
    current = int(get_setting("data_version", "1")) + 1
    set_setting("data_version", current)
    return current


def storage_name() -> str:
    initialize_database()
    return "PostgreSQL / Neon" if get_engine().dialect.name == "postgresql" else "SQLite (локально)"
