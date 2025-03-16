from typing import Any, Dict

import structlog
from shared.schemas.message import TrackerMessage

from app.celery_app import app
from app.consumers.adventure_boss_consumer import consume_adventure_boss_message

logger = structlog.get_logger(__name__)


@app.task(
    name="season_pass.process_adventure_boss",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    acks_late=True,
)
def process_adventure_boss(self, message: Dict[str, Any]) -> str:
    """
    Process adventure boss messages as a Celery task

    Args:
        self: Task instance
        message: The message data from RabbitMQ/task queue

    Returns:
        str: Processing result message
    """
    try:
        logger.info("Processing adventure boss message", message=message)
        tracker_message = TrackerMessage.model_validate(message)
        consume_adventure_boss_message(tracker_message)
        return "Adventure boss message processed successfully"
    except Exception as exc:
        logger.error(
            "Error processing adventure boss message", message=message, exc_info=exc
        )
        self.retry(exc=exc)
