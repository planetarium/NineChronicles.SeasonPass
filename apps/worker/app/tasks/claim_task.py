from typing import Any, Dict

import structlog
from shared.schemas.message import ClaimMessage

from app.celery_app import app
from app.consumers.claim_consumer import consume_claim_message

logger = structlog.get_logger(__name__)


@app.task(
    name="season_pass.process_claim",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    acks_late=True,
    priority=0,
    queue="claim_queue",
)
def process_claim(self, message: Dict[str, Any]) -> str:
    """
    Process claim messages as a Celery task

    Args:
        self: Task instance
        message: The message data from RabbitMQ/task queue

    Returns:
        str: Processing result message
    """
    try:
        logger.info("Processing claim message", message=message)
        claim_message = ClaimMessage.model_validate(message)
        consume_claim_message(claim_message)
        return "Claim message processed successfully"
    except Exception as exc:
        logger.error("Error processing claim message", message=message, exc_info=exc)
        self.retry(exc=exc)
