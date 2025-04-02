import concurrent.futures
import json
import time
from collections import defaultdict

import structlog
from shared.enums import PassType
from shared.models.action import Block
from shared.schemas.message import TrackerMessage
from sqlalchemy import create_engine, select
from sqlalchemy.orm import scoped_session, sessionmaker

from app.celery import send_to_worker
from app.config import config
from app.consumers.world_clear_consumer import consume_world_clear_message
from app.schemas.action import ActionJson
from app.utils.gql import fetch_block_data, get_block_tip

logger = structlog.get_logger(__name__)
engine = create_engine(str(config.pg_dsn))


def track_world_clear_actions(planet_id: str, gql_url: str, block_index: int):
    tx_data, tx_result_list = fetch_block_data(
        gql_url,
        block_index,
        PassType.WORLD_CLEAR_PASS,
        config.headless_jwt_secret,
    )

    action_data = defaultdict(list)
    agent_list = set()
    for i, tx in enumerate(tx_data):
        if tx_result_list[i] != "SUCCESS":
            continue

        for action in tx["actions"]:
            action_raw = json.loads(action["json"].replace(r"\uFEFF", ""))
            type_id = action_raw["type_id"]

            agent_list.add(tx["signer"].lower())
            action_json = ActionJson(type_id=type_id, **(action_raw["values"]))
            action_data[action_json.type_id].append(
                {
                    "tx_id": tx["id"],
                    "agent_addr": tx["signer"].lower(),
                    "avatar_addr": action_json.avatar_addr.lower(),
                    "world_id": (action_json.worldId),
                    "stage_id": (action_json.stageId),
                }
            )

    logger.info(
        f"Sending task to Celery worker: season_pass.process_world_clear",
        planet_id=planet_id,
        block=block_index,
        action_count=len(action_data),
    )
    message = TrackerMessage(
        planet_id=planet_id,
        block=block_index,
        action_data=action_data,
    )
    consume_world_clear_message(message)


def track_missing_blocks():
    for planet_id, gql_url in config.gql_url_map.items():
        if planet_id not in config.enabled_planets:
            continue

        sess = scoped_session(sessionmaker(bind=engine))
        try:
            # Get missing blocks
            start_block = config.start_block_index_map[planet_id]
            expected_all = set(
                range(start_block, get_block_tip(gql_url, config.headless_jwt_secret))
            )
            all_blocks = set(
                sess.scalars(
                    select(Block.index)
                    .where(
                        Block.planet_id == planet_id.encode(),
                        Block.pass_type == PassType.WORLD_CLEAR_PASS,
                        Block.index >= start_block,
                    )
                ).fetchall()
            )
            missing_blocks = expected_all - all_blocks
        except Exception as e:
            raise e
        finally:
            sess.close()

        logger.info(
            "Missing blocks",
            tracker="world_clear_tracker",
            missing_blocks=[missing_blocks],
            planet_id=planet_id,
            start_block=start_block,
        )

        block_dict = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            for index in missing_blocks:
                block_dict[
                    executor.submit(track_world_clear_actions, planet_id, gql_url, index)
                ] = index
            for future in concurrent.futures.as_completed(block_dict):
                index = block_dict[future]
                exc = future.exception()
                if exc:
                    logger.exception(
                        "Error occurred processing block",
                        tracker="world_clear_tracker",
                        exc=exc,
                    )
                else:
                    result = future.result()
                    logger.info(f"Block {index} collected :: {result}")
