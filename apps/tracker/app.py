import signal
import sys
import threading
import time
from typing import Callable

import structlog

from app.config import config
from app.trackers.adv_boss_tracker import (
    track_missing_blocks as track_adv_boss_missing_blocks,
)
from app.trackers.courage_tracker import (
    track_missing_blocks as track_courage_missing_blocks,
)
from app.trackers.tx_tracker import track_tx
from app.trackers.world_clear_tracker import (
    track_missing_blocks as track_world_clear_missing_blocks,
)

logger = structlog.get_logger(__name__)
running = True


def runner(name: str, func: Callable, interval: int):
    logger.info(f"Starting {name} tracker")

    while running:
        try:
            func()
            logger.info(f"{name} tracker completed cycle")
        except Exception as e:
            logger.error(f"Error in {name} tracker", exc_info=e)

        time.sleep(interval)


def signal_handler(sig, frame):
    global running
    logger.info("Shutting down trackers...")
    running = False
    sys.exit(0)


def main():
    global running
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    trackers = [
        (
            "AdventureBossTracker",
            lambda: track_adv_boss_missing_blocks(),
            8,
        ),
        (
            "CourageTracker",
            lambda: track_courage_missing_blocks(),
            8,
        ),
        (
            "WorldClearTracker",
            lambda: track_world_clear_missing_blocks(),
            8,
        ),
        ("TxTracker", track_tx, 4),
    ]

    threads = []
    for name, func, interval in trackers:
        thread = threading.Thread(
            target=runner, args=(name, func, interval), daemon=True
        )
        thread.start()
        threads.append(thread)
        logger.info(f"{name} thread started")

    try:
        while running:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received, shutting down...")
        running = False

    for thread in threads:
        thread.join()

    logger.info("All trackers stopped")


if __name__ == "__main__":
    logger.info(f"Starting trackers")
    main()
