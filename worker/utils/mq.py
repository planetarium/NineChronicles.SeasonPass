import json
import logging
import os
from collections import defaultdict

from pika import BlockingConnection, ConnectionParameters

from common.enums import PlanetID

queue_host = os.environ["SQS_URL"]


def send_message(planet: PlanetID, queue: str, index: int, action_data: defaultdict):
    message = {
        "planet_id": planet.value.decode(),
        "block": index,
        "action_data": dict(action_data),
    }
    connection = get_connection()
    channel = connection.channel()
    channel.queue_declare(queue=queue)
    channel.basic_publish(exchange="", routing_key=queue, body=json.dumps(message))
    logging.info(f"Message {message} sent to queue for block {index}.")
    connection.close()


def get_connection() -> BlockingConnection:
    connection = BlockingConnection(ConnectionParameters(queue_host))
    return connection
