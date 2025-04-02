#!/usr/bin/env python
import argparse
import json
import logging
import os
import uuid
from typing import Any, Dict, Optional

from celery import Celery
from dotenv import load_dotenv

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


class ClaimMessage:
    """시즌 패스 보상 클레임 메시지 클래스"""

    def __init__(self, uuid: str):
        self.uuid = uuid

    def model_dump(self) -> Dict[str, Any]:
        """메시지를 딕셔너리로 변환"""
        return {"uuid": self.uuid}


def send_to_worker(task_name: str, payload: Dict[str, Any]) -> Optional[str]:
    """Celery 워커에 작업을 전송하는 함수

    Args:
        task_name: 실행할 Celery 작업 이름
        payload: 작업에 전달할 데이터

    Returns:
        task_id: 생성된 Celery 작업의 ID
    """
    # .env 파일에서 환경 변수 로드
    load_dotenv()

    # Celery 환경 설정
    broker_url = os.environ.get("CELERY_BROKER_URL")
    result_backend = os.environ.get("CELERY_RESULT_BACKEND")

    if not broker_url or not result_backend:
        logger.error(
            "환경 변수 CELERY_BROKER_URL 또는 CELERY_RESULT_BACKEND가 설정되지 않았습니다."
        )
        return None

    app = Celery(
        "season_pass_worker",
        broker=broker_url,
        backend=result_backend,
    )

    app.conf.update(
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        timezone="UTC",
        enable_utc=True,
    )

    # 작업 보내기
    try:
        task = app.send_task(task_name, args=[payload], queue="claim_queue")
        logger.info(
            f"작업 {task_name}을(를) Celery 워커에 전송했습니다. 작업 ID: {task.id}"
        )
        return task.id
    except Exception as e:
        logger.error(f"작업 전송 중 오류 발생: {e}")
        return None


def main():
    """메인 함수"""
    parser = argparse.ArgumentParser(
        description="Celery 워커에 시즌 패스 보상 클레임 요청을 전송합니다."
    )
    parser.add_argument("uuid", help="클레임의 UUID")

    args = parser.parse_args()

    # 클레임 메시지 생성
    claim_message = ClaimMessage(uuid=args.uuid)

    # 워커에 작업 전송
    task_id = send_to_worker("season_pass.process_claim", claim_message.model_dump())

    if task_id:
        print(f"성공: 작업이 전송되었습니다. 작업 ID: {task_id}")
    else:
        print("오류: 작업 전송 실패. 로그를 확인하세요.")


if __name__ == "__main__":
    main()
