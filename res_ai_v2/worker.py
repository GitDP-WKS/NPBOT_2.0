from __future__ import annotations

import argparse
import logging
import signal
import threading
import time
from collections.abc import Callable

from .agent import run_agent_cycle
from .agent_monitor import recover_stale_events
from .db import initialize_database

LOGGER = logging.getLogger("res_ai_agent")


def run_worker(
    *,
    stop_event: threading.Event,
    idle_seconds: float = 2.0,
    max_events: int = 100,
    stale_check_every: int = 30,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Постоянно обрабатывает общую очередь событий до сигнала остановки."""
    initialize_database()
    cycles = 0
    while not stop_event.is_set():
        cycles += 1
        if cycles == 1 or cycles % max(1, stale_check_every) == 0:
            recovered = recover_stale_events()
            if recovered:
                LOGGER.warning("Возвращено зависших событий: %s", recovered)

        result = run_agent_cycle(max_events=max_events)
        if result.failed:
            LOGGER.error(
                "Цикл агента завершен с ошибками: обработано=%s, ошибок=%s",
                result.processed,
                result.failed,
            )
        elif result.processed:
            LOGGER.info(
                "Обработано событий: %s, завершено: %s",
                result.processed,
                result.completed,
            )

        if result.processed == 0 and not stop_event.is_set():
            sleep(max(0.1, idle_seconds))


def main() -> None:
    parser = argparse.ArgumentParser(description="Постоянный обработчик событий РЭС AI")
    parser.add_argument("--idle-seconds", type=float, default=2.0)
    parser.add_argument("--max-events", type=int, default=100)
    parser.add_argument("--stale-check-every", type=int, default=30)
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )
    stop_event = threading.Event()

    def request_stop(_signum, _frame) -> None:
        LOGGER.info("Получен сигнал остановки. Завершаю текущий цикл.")
        stop_event.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    run_worker(
        stop_event=stop_event,
        idle_seconds=args.idle_seconds,
        max_events=max(1, args.max_events),
        stale_check_every=max(1, args.stale_check_every),
    )


if __name__ == "__main__":
    main()
