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
    # (2026-09-29) 결과 백엔드를 태스크 발행 경로에서 뗀다. beat 데드락의 근본 원인이다.
    #   celery 의 send_task 는 `if not ignore_result: self.backend.on_task_call(...)` 이라,
    #   결과를 안 쓰겠다고 선언하면 발행 때마다 Redis 에 PubSub SUBSCRIBE 를 거는 경로가
    #   통째로 사라진다. 그 경로에서 이런 일이 벌어졌다(자매 서비스 IAP 에서 py-spy 로 확인):
    #     send_task → on_task_call → PubSub.subscribe(SUBSCRIBE 전송) 도중
    #     GC 가 AsyncResult.__del__ 을 실행 → remove_pending_result → cancel_for →
    #     **같은 PubSub 커넥션에 UNSUBSCRIBE 재진입** → redis/client.py 에서 영구 블록.
    #   seasonpass-beat 은 이 상태로 **2일 18시간**(2026-09-26 01:33 UTC~) 멈춰 있었다.
    #   파드는 내내 Running 1/1 이었고 재시작도 0회라 k8s 로는 보이지 않았다. 그동안
    #   process_retry_claim / process_retry_stage 가 통째로 멈췄다 — 일반 클레임은 api 가
    #   직접 발행하므로 정상이었고, **실패한 클레임의 복구 경로만** 조용히 죽어 있었다.
    #   끌 수 있는 근거: 이 저장소에는 AsyncResult/.ready()/.get() 사용처가 하나도 없다.
    #   flower 도 안 잃는다 — 결과는 워커의 `task-succeeded` **이벤트**로 가고(celery 소스상
    #   ignore_result 와 무관하다), 결과 백엔드를 거치지 않는다.
    task_ignore_result=True,
    # ⚠️ ignore_result 가 막는 건 **등록된 Task 의 apply_async 경로**(= beat)뿐이다.
    #   celery 의 send_task 는 conf 를 안 보고 options 만 보므로(`options.pop(...)`),
    #   send_task 호출부에는 인자로 따로 넘겨야 한다(api·tracker 의 send_to_worker 참고).
    #
    # 아래는 그 뒤에 남는 방어선. 이 값들은 **결과 백엔드 커넥션 전용**이다
    #   (브로커는 RabbitMQ 이고 broker_transport_options 를 따로 본다).
    #   기본값이 전부 None = 영원히 블록이라, 그래서 위 데드락이 스스로 풀릴 길이 없었다.
    redis_socket_timeout=5.0,
    redis_socket_connect_timeout=5.0,
    redis_socket_keepalive=True,
    redis_retry_on_timeout=True,
    # beat 의 tick 주기를 고정한다. 기본값(300초)이면 beat 이 다음 due 까지 자느라
    #   스케줄 파일 갱신이 들쭉날쭉해서, 9c-infra 의 beat liveness probe 가 임계값을
    #   900초로 크게 잡아야 한다. 60초로 고정하면 420초까지 좁힐 수 있다.
    beat_max_loop_interval=60,
    timezone="UTC",
    enable_utc=True,
    # 같은 서명 키로 nonce 를 순서대로 매기므로 한 번에 하나만 처리한다. 실제 실행 인자
    #   (9c-infra 차트, Dockerfile.Worker)도 --concurrency=1 이다. 코드의 잠금(claim 행·플래닛
    #   advisory lock)은 이 값이 바뀌거나 파드가 겹칠 때를 위한 방어선이다.
    worker_concurrency=1,
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
    # expires: 워커가 멈췄다 돌아오면 5분마다 쌓인 재처리 작업이 한꺼번에 돌지 않게, 다음
    #   주기(300초)가 오기 전에 실행되지 못한 작업은 버린다. 다음 beat 이 같은 일을 다시 넣는다.
    #   celery 의 self.retry 는 원래 요청의 만료 시각(절대 시각)을 그대로 물려받는다
    #   (Context.as_execution_options 의 'expires'). 그래서 발행 240초 뒤를 넘는 재시도는 실행
    #   직전에 버려진다 — 다음 주기가 대신하므로 의도한 동작이다.
    #   주의: 동시 처리 1 이라 앞 작업(retry_claim 최대 100건)이 240초를 넘기면 같은 주기의
    #   retry_stage 가 버려진다. 버려짐은 celery 의 info 로그('Discarding revoked task')뿐이다.
    beat_schedule={
        "retry-stage-every-5-minutes": {
            "task": "season_pass.process_retry_stage",
            "schedule": 300.0,
            "options": {"queue": "claim_queue", "expires": 240},
        },
        "retry-claim-every-5-minutes": {
            "task": "season_pass.process_retry_claim",
            "schedule": 300.0,
            "options": {"queue": "claim_queue", "expires": 240},
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
