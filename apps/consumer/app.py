import json
import signal
import sys
import threading
from typing import Any, Callable, Dict

import pika
import structlog
from app.config import config
from app.consumers.adventure_boss_consumer import consume_adventure_boss_message
from app.consumers.claim_consumer import consume_claim_message
from app.consumers.courage_consumer import consume_courage_message
from app.consumers.world_clear_consumer import consume_world_clear_message
from pika.adapters.blocking_connection import BlockingChannel
from pika.spec import Basic, BasicProperties

from shared.constants import (
    ADVENTURE_BOSS_QUEUE_NAME,
    CLAIM_QUEUE_NAME,
    COURAGE_QUEUE_NAME,
    WORLD_CLEAR_QUEUE_NAME,
)

logger = structlog.get_logger(__name__)
running = True


HANDLERS = {
    ADVENTURE_BOSS_QUEUE_NAME: consume_adventure_boss_message,
    COURAGE_QUEUE_NAME: consume_courage_message,
    WORLD_CLEAR_QUEUE_NAME: consume_world_clear_message,
    CLAIM_QUEUE_NAME: consume_claim_message,
}


def signal_handler(sig, frame):
    global running
    logger.info("Shutting down consumer...")
    running = False
    sys.exit(0)


def message_callback(
    channel: BlockingChannel,
    method: Basic.Deliver,
    properties: BasicProperties,
    body: bytes,
    handler: Callable[[Dict[str, Any]], None],
):
    """Process incoming message from RabbitMQ."""
    logger.info(f"Received message from {method.routing_key}")
    try:
        message = json.loads(body.decode("utf-8"))
        logger.debug(f"Message content: {message}")

        # Process the message using the appropriate handler
        handler(message)

        # Acknowledge the message
        channel.basic_ack(delivery_tag=method.delivery_tag)
        logger.info(f"Successfully processed message from {method.routing_key}")
    except Exception as e:
        logger.error(
            f"Error processing message from {method.routing_key}",
            exc_info=e,
        )
        # Negative acknowledge to requeue the message
        channel.basic_nack(delivery_tag=method.delivery_tag, requeue=True)


def setup_consumer(
    rmq_connection: pika.BlockingConnection,
    queue_name: str,
    handler: Callable[[Dict[str, Any]], None],
):
    """Set up a consumer for a specific queue."""
    channel = rmq_connection.channel()

    # Ensure the queue exists
    channel.queue_declare(queue=queue_name, durable=True)

    # Set QoS to limit the number of unacknowledged messages
    channel.basic_qos(prefetch_count=1)

    # Set up the callback for this queue
    callback = lambda ch, method, properties, body: message_callback(
        ch, method, properties, body, handler
    )

    # Start consuming
    channel.basic_consume(
        queue=queue_name,
        on_message_callback=callback,
        auto_ack=False,
    )

    logger.info(f"Consumer set up for queue: {queue_name}")
    return channel


def consumer_thread(queue_name: str, handler: Callable[[Dict[str, Any]], None]):
    """Thread function to run a consumer for a specific queue."""
    logger.info(f"Starting consumer for {queue_name}")

    while running:
        try:
            # Create a new connection
            connection = pika.BlockingConnection(
                pika.URLParameters(str(config.amqp_dsn))
            )

            # Set up the consumer
            channel = setup_consumer(connection, queue_name, handler)

            # Start consuming messages
            logger.info(f"Consuming messages from {queue_name}...")
            while running and connection.is_open:
                connection.process_data_events(
                    time_limit=1
                )  # Process messages with timeout

            # Close the connection if we're exiting
            if connection.is_open:
                connection.close()

        except pika.exceptions.AMQPConnectionError as e:
            logger.error(
                f"Connection error for {queue_name}, reconnecting...", exc_info=e
            )
        except Exception as e:
            logger.error(f"Unexpected error in {queue_name} consumer", exc_info=e)

        # Only try to reconnect if we're still running
        if running:
            logger.info(f"Reconnecting consumer for {queue_name} in 5 seconds...")
            import time

            time.sleep(5)


def main():
    global running
    logger.info(f"Starting consumers")

    # Register signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Start a thread for each queue
    threads = []
    for queue_name, handler in HANDLERS.items():
        thread = threading.Thread(
            target=consumer_thread,
            args=(queue_name, handler),
            daemon=True,
        )
        thread.start()
        threads.append(thread)
        logger.info(f"Consumer thread started for {queue_name}")

    # Keep the main thread alive
    try:
        while running:
            import time

            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received, shutting down...")
        running = False

    # Wait for all threads to finish
    for thread in threads:
        thread.join()

    logger.info("All consumers stopped")


if __name__ == "__main__":
    main()
