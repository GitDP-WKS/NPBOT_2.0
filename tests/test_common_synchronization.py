from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from res_ai_v2.db import get_setting, increment_setting
from res_ai_v2.event_bus import claim_next_event, complete_event, publish_event, queue_status
from res_ai_v2.knowledge_agent import KNOWLEDGE_LOCK, rebuild_knowledge
from res_ai_v2.synchronization import (
    SynchronizationBusy,
    acquire_agent_lock,
    release_agent_lock,
)


def test_only_one_process_holds_knowledge_lock(temp_db) -> None:
    first = acquire_agent_lock(KNOWLEDGE_LOCK, owner="worker-one", lease_seconds=60)
    try:
        with pytest.raises(SynchronizationBusy):
            acquire_agent_lock(
                KNOWLEDGE_LOCK,
                owner="worker-two",
                lease_seconds=60,
                wait_seconds=0,
            )
        with pytest.raises(SynchronizationBusy):
            rebuild_knowledge(
                full_rebuild=True,
                trigger_type="parallel-test",
                trigger_key="blocked",
            )
    finally:
        assert release_agent_lock(KNOWLEDGE_LOCK, first) is True

    second = acquire_agent_lock(KNOWLEDGE_LOCK, owner="worker-two", lease_seconds=60)
    assert second == "worker-two"
    assert release_agent_lock(KNOWLEDGE_LOCK, second) is True


def test_parallel_counter_does_not_lose_updates(temp_db) -> None:
    workers = 6
    increments_per_worker = 30
    barrier = threading.Barrier(workers)

    def increment_many() -> None:
        barrier.wait()
        for _ in range(increments_per_worker):
            increment_setting("parallel_test_counter", 1, default=0)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(increment_many) for _ in range(workers)]
        for future in futures:
            future.result(timeout=30)

    assert int(get_setting("parallel_test_counter", "0")) == workers * increments_per_worker


def test_events_are_claimed_once_by_parallel_workers(temp_db) -> None:
    total = 40
    expected = {
        publish_event(
            "parallel_test_event",
            "test",
            str(index),
            {"index": index},
            deduplication_key=f"parallel:{index}",
        )
        for index in range(total)
    }
    claimed: list[int] = []
    claimed_lock = threading.Lock()
    start = threading.Barrier(4)

    def consume(worker: str) -> None:
        start.wait()
        while True:
            event = claim_next_event(worker)
            if event is None:
                return
            with claimed_lock:
                claimed.append(event.id)
            complete_event(event.id, {"worker": worker})

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(consume, f"worker-{index}") for index in range(4)]
        for future in futures:
            future.result(timeout=30)

    assert set(claimed) == expected
    assert len(claimed) == total
    assert len(set(claimed)) == total
    assert queue_status().get("completed") == total
