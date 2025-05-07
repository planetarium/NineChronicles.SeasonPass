import structlog
from app.config import config
from celery import Celery
from kombu import Exchange, Queue

logger = structlog.get_logger(__name__)

task_exchange = Exchange("tasks", type="direct")

claim_queue = Queue(
    "claim_queue",
    exchange=task_exchange,
    routing_key="claim_tasks",
)

app = Celery(
    "season_pass_worker", broker=config.broker_url, backend=config.result_backend
)

app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    worker_concurrency=4,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_queues=(claim_queue,),
    task_default_queue="claim_queue",
    task_default_exchange="tasks",
    task_default_routing_key="claim_tasks",
    task_create_missing_queues=True,
    task_default_delivery_mode="persistent",
    worker_direct=True,
    beat_schedule={
        "retry-stage-every-5-minutes": {
            "task": "season_pass.process_retry_stage",
            "schedule": 300.0,
            "options": {"queue": "claim_queue"},
        },
        "retry-claim-every-5-minutes": {
            "task": "season_pass.process_retry_claim",
            "schedule": 300.0,
            "options": {"queue": "claim_queue"},
        },
    },
)

app.autodiscover_tasks(["app.tasks"])


@app.on_after_configure.connect
def setup_periodic_tasks(sender, **kwargs):
    logger.info("Setting up periodic tasks")


@app.task(bind=True)
def debug_task(self):
    """Task for debugging purposes"""
    logger.info(f"Request: {self.request!r}")
    return "Debug task completed"
