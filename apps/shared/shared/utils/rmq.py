import json
from typing import Any, Callable, Optional

import pika
from pika.adapters.blocking_connection import BlockingChannel, BlockingConnection
from pika.spec import Basic, BasicProperties


class RabbitMQ:
    def __init__(self, amqp_dsn: str):
        self.amqp_dsn = amqp_dsn
        self.connection: Optional[BlockingConnection] = None
        self.channel: Optional[BlockingChannel] = None

    def connect(self) -> None:
        parameters = pika.URLParameters(self.amqp_dsn)
        self.connection = pika.BlockingConnection(parameters)
        self.channel = self.connection.channel()

    def close(self) -> None:
        if self.channel:
            self.channel.close()
        if self.connection:
            self.connection.close()
        self.channel = None
        self.connection = None

    def declare_queue(
        self, queue_name: str, durable: bool = True, auto_delete: bool = False
    ) -> None:
        if not self.channel:
            self.connect()
        self.channel.queue_declare(
            queue=queue_name, durable=durable, auto_delete=auto_delete
        )

    def declare_exchange(
        self, exchange_name: str, exchange_type: str = "direct", durable: bool = True
    ) -> None:
        if not self.channel:
            self.connect()
        self.channel.exchange_declare(
            exchange=exchange_name, exchange_type=exchange_type, durable=durable
        )

    def bind_queue(
        self, queue_name: str, exchange_name: str, routing_key: str = ""
    ) -> None:
        if not self.channel:
            self.connect()
        self.channel.queue_bind(
            queue=queue_name, exchange=exchange_name, routing_key=routing_key
        )

    def publish(
        self,
        exchange: str,
        routing_key: str,
        body: Any,
        properties: Optional[BasicProperties] = None,
    ) -> None:
        if not self.channel:
            self.connect()

        if isinstance(body, (dict, list)):
            body = json.dumps(body)
        if isinstance(body, str):
            body = body.encode("utf-8")

        self.channel.basic_publish(
            exchange=exchange,
            routing_key=routing_key,
            body=body,
            properties=properties
            or pika.BasicProperties(
                delivery_mode=2,
            ),
        )

    def consume(
        self,
        queue: str,
        callback: Callable[
            [BlockingChannel, Basic.Deliver, BasicProperties, bytes], None
        ],
        auto_ack: bool = False,
    ) -> None:
        if not self.channel:
            self.connect()

        self.channel.basic_consume(
            queue=queue, on_message_callback=callback, auto_ack=auto_ack
        )
        self.channel.start_consuming()

    def ack(self, delivery_tag: int) -> None:
        if not self.channel:
            raise Exception("Channel not established")
        self.channel.basic_ack(delivery_tag=delivery_tag)

    def nack(self, delivery_tag: int, requeue: bool = True) -> None:
        if not self.channel:
            raise Exception("Channel not established")
        self.channel.basic_nack(delivery_tag=delivery_tag, requeue=requeue)

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
