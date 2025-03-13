import concurrent.futures
import json
import time
from collections import defaultdict

import structlog
from app.config import config
from app.constants import ADVENTURE_BOSS_QUEUE_NAME
from app.schemas.action import AdventureBossActionJson
from app.schemas.message import Message
from app.utils.gql import fetch_block_data, get_block_tip
from sqlalchemy import create_engine, select
from sqlalchemy.orm import scoped_session, sessionmaker

from shared.enums import PassType
from shared.models.action import Block
from shared.utils.rmq import RabbitMQ

logger = structlog.get_logger(__name__)
engine = create_engine(str(config.pg_dsn), pool_size=5, max_overflow=5)


def track_adv_boss_actions(rmq: RabbitMQ, block_index: int):
    tx_data, tx_result_list = fetch_block_data(
        config.gql_url,
        block_index,
        PassType.ADVENTURE_BOSS_PASS,
        config.headless_jwt_secret,
    )
    time.sleep(0.2)

    action_data = defaultdict(list)
    for i, tx in enumerate(tx_data):
        if tx_result_list[i] != "SUCCESS":
            continue

        for action in tx["actions"]:
            action_raw = json.loads(action["json"].replace(r"\uFEFF", ""))
            type_id = action_raw["type_id"]

            action_json = AdventureBossActionJson(
                type_id=type_id, **(action_raw["values"])
            )
            action_data[action_json.type_id].append(
                {
                    "tx_id": tx["id"],
                    "season_index": action_json.season_index,
                    "agent_addr": tx["signer"].lower(),
                    "avatar_addr": action_json.avatar_addr.lower(),
                    "count_base": action_json.count_base,
                }
            )

    logger.info(
        f"Publish to {ADVENTURE_BOSS_QUEUE_NAME}",
        tracker="adv_boss_tracker",
        publish_count=len(action_data),
        planet_id=config.planet_id.decode(),
        block=block_index,
    )
    rmq.publish(
        exchange=config.planet_id.value,
        routing_key=ADVENTURE_BOSS_QUEUE_NAME,
        body=Message(
            planet_id=config.planet_id.decode(),
            block=block_index,
            action_data=action_data,
        ).to_dict(),
    )


def track_missing_blocks(rmq: RabbitMQ):
    sess = scoped_session(sessionmaker(bind=engine))
    # Get missing blocks
    start_block = config.start_block_index
    expected_all = set(
        range(start_block, get_block_tip(config.gql_url, config.headless_jwt_secret))
    )  # Sloth needs 1 block to render actions: get tip-1
    all_blocks = set(
        sess.scalars(
            select(Block.index).where(
                Block.planet_id == config.planet_id,
                Block.index >= start_block,
                Block.pass_type == PassType.ADVENTURE_BOSS_PASS,
            )
        ).fetchall()
    )
    missing_blocks = expected_all - all_blocks
    sess.close()

    logger.info(
        "Missing blocks",
        tracker="adv_boss_tracker",
        missing_blocks=[missing_blocks],
        planet_id=config.planet_id.decode(),
        start_block=start_block,
    )

    block_dict = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        for index in missing_blocks:
            block_dict[executor.submit(track_adv_boss_actions, rmq, index)] = index
        for future in concurrent.futures.as_completed(block_dict):
            index = block_dict[future]
            exc = future.exception()
            if exc:
                logger.exception(
                    "Error occurred processing block",
                    tracker="adv_boss_tracker",
                    exc=exc,
                )
            else:
                result = future.result()
                logger.info(f"Block {index} collected :: {result}")
