from __future__ import annotations

from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
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
    elif cfg.database_url.startswith("sqlite:///"):
        sqlite_path = cfg.database_url.removeprefix("sqlite:///")
        if sqlite_path and sqlite_path != ":memory:":
            Path(sqlite_path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
    return create_engine(cfg.database_url, **kwargs)


@lru_cache(maxsize=1)
def initialize_database() -> None:
    engine = get_engine()
    from .migrations import run_migrations
    from .pit_bootstrap import BOOTSTRAP_SETTING, bootstrap_current_knowledge

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
        defaults = {
            "data_version": "1",
            "human_decisions_since_training": "0",
        }
        for key, value in defaults.items():
            if not conn.execute(select(settings.c.key).where(settings.c.key == key)).first():
                conn.execute(
                    insert(settings).values(
                        key=key,
                        value=value,
                        updated_at=now,
                    )
                )

        bootstrapped = conn.execute(
            select(settings.c.value).where(settings.c.key == BOOTSTRAP_SETTING)
        ).first()
        if not bootstrapped:
            result = bootstrap_current_knowledge(conn, now)
            conn.execute(
                insert(settings).values(
                    key=BOOTSTRAP_SETTING,
                    value=f"done:{result['observations']}:{result['directives']}",
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
            conn.execute(
                update(settings)
                .where(settings.c.key == key)
                .values(value=str(value), updated_at=now)
            )
        else:
            conn.execute(
                insert(settings).values(
                    key=key,
                    value=str(value),
                    updated_at=now,
                )
            )


def bump_data_version() -> int:
    current = int(get_setting("data_version", "1")) + 1
    set_setting("data_version", current)
    return current


def storage_name() -> str:
    initialize_database()
    return "PostgreSQL / Neon" if get_engine().dialect.name == "postgresql" else "SQLite (локально)"
