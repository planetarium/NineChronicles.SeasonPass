import json
import os
from collections import defaultdict

import boto3

from common import logger
from common.enums import PlanetID


def send_sqs_message(region: str, planet: PlanetID, queue: str, index: int, action_data: defaultdict):
    sqs = boto3.client("sqs", region_name=region)
    message = {
        "planet_id": planet.value.decode(),
        "block": index,
        "action_data": dict(action_data),
    }
    resp = sqs.send_message(
        QueueUrl=queue,
        MessageBody=json.dumps(message),
    )
    logger.info(f"Message {resp['MessageId']} sent to SQS for block {index}.")
