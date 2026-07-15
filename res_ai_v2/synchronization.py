from __future__ import annotations

import socket
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import delete, insert, or_, select, update
from sqlalchemy.exc import IntegrityError, OperationalError

from .db import get_engine, initialize_database, utcnow
from .pit_schema import agent_locks


class SynchronizationBusy(RuntimeError):
    """Запрошенная общая операция уже выполняется другим экземпляром агента."""


def lock_owner(prefix: str = "agent") -> str:
    host = socket.gethostname() or "res-ai"
    return f"{prefix}:{host}:{uuid4().hex}"


def _database_busy(exc: OperationalError) -> bool:
    text = str(exc).lower()
    return "locked" in text or "busy" in text or "could not obtain lock" in text


def _try_acquire(lock_key: str, owner: str, lease_seconds: int) -> bool:
    now = utcnow()
    lease_until = now + timedelta(seconds=max(1, lease_seconds))
    with get_engine().begin() as conn:
        updated = conn.execute(
            update(agent_locks)
            .where(
                agent_locks.c.lock_key == lock_key,
                or_(
                    agent_locks.c.lease_until <= now,
                    agent_locks.c.owner == owner,
                ),
            )
            .values(owner=owner, lease_until=lease_until, updated_at=now)
        )
        if updated.rowcount == 1:
            return True
        try:
            with conn.begin_nested():
                conn.execute(
                    insert(agent_locks).values(
                        lock_key=lock_key,
                        owner=owner,
                        lease_until=lease_until,
                        created_at=now,
                        updated_at=now,
                    )
                )
            return True
        except IntegrityError:
            return False


def acquire_agent_lock(
    lock_key: str,
    *,
    owner: str | None = None,
    lease_seconds: int = 1800,
    wait_seconds: float = 0.0,
    poll_seconds: float = 0.05,
) -> str:
    """Атомарно резервирует общую операцию во всех процессах приложения."""
    initialize_database()
    normalized = lock_key.strip()
    if not normalized:
        raise ValueError("Не указан ключ синхронизации.")
    token = owner or lock_owner(normalized)
    deadline = time.monotonic() + max(0.0, wait_seconds)
    while True:
        try:
            if _try_acquire(normalized, token, lease_seconds):
                return token
        except OperationalError as exc:
            if not _database_busy(exc):
                raise
        if time.monotonic() >= deadline:
            raise SynchronizationBusy(
                f"Операция {normalized!r} уже выполняется другим экземпляром агента."
            )
        time.sleep(max(0.01, poll_seconds))


def renew_agent_lock(lock_key: str, owner: str, *, lease_seconds: int = 1800) -> bool:
    now = utcnow()
    with get_engine().begin() as conn:
        result = conn.execute(
            update(agent_locks)
            .where(
                agent_locks.c.lock_key == lock_key,
                agent_locks.c.owner == owner,
            )
            .values(
                lease_until=now + timedelta(seconds=max(1, lease_seconds)),
                updated_at=now,
            )
        )
    return result.rowcount == 1


def release_agent_lock(lock_key: str, owner: str) -> bool:
    with get_engine().begin() as conn:
        result = conn.execute(
            delete(agent_locks).where(
                agent_locks.c.lock_key == lock_key,
                agent_locks.c.owner == owner,
            )
        )
    return result.rowcount == 1


@contextmanager
def agent_lock(
    lock_key: str,
    *,
    owner: str | None = None,
    lease_seconds: int = 1800,
    wait_seconds: float = 0.0,
    poll_seconds: float = 0.05,
) -> Iterator[str]:
    token = acquire_agent_lock(
        lock_key,
        owner=owner,
        lease_seconds=lease_seconds,
        wait_seconds=wait_seconds,
        poll_seconds=poll_seconds,
    )
    try:
        yield token
    finally:
        release_agent_lock(lock_key, token)


def active_agent_locks() -> list[dict[str, object]]:
    now = utcnow()
    with get_engine().connect() as conn:
        rows = conn.execute(
            select(agent_locks)
            .where(agent_locks.c.lease_until > now)
            .order_by(agent_locks.c.lock_key)
        )
    return [dict(row._mapping) for row in rows]
