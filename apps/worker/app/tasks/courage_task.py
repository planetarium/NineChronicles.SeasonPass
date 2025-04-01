from typing import Any, Dict

import structlog
from shared.schemas.message import TrackerMessage

from app.celery_app import app
from app.consumers.courage_consumer import consume_courage_message

logger = structlog.get_logger(__name__)


@app.task(
    name="season_pass.process_courage",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    acks_late=True,
    priority=1,
    queue="tracker_queue",
)
def process_courage(self, message: Dict[str, Any]) -> str:
    """
    Process courage messages as a Celery task

    Args:
        self: Task instance
        message: The message data from RabbitMQ/task queue

    Returns:
        str: Processing result message
    """
    try:
        logger.info("Processing courage message", message=message)
        tracker_message = TrackerMessage.model_validate(message)
        consume_courage_message(tracker_message)
        return "Courage message processed successfully"
    except Exception as exc:
        logger.error("Error processing courage message", message=message, exc_info=exc)
        self.retry(exc=exc)
