import json
from typing import Any, Optional

import pika
from pika.adapters.blocking_connection import BlockingChannel, BlockingConnection


class RabbitMQ:
    def __init__(self, amqp_dsn: str):
        self.amqp_dsn = amqp_dsn
        self.channel: Optional[BlockingChannel] = None

    def get_connection(self) -> BlockingConnection:
        return BlockingConnection(pika.URLParameters(self.amqp_dsn))

    def publish(
        self,
        routing_key: str,
        body: Any,
    ) -> None:
        connection = self.get_connection()
        channel = connection.channel()

        if isinstance(body, (dict, list)):
            body = json.dumps(body)
        if isinstance(body, str):
            body = body.encode("utf-8")

        channel.basic_publish(
            exchange="",
            routing_key=routing_key,
            body=body,
        )
        connection.close()
