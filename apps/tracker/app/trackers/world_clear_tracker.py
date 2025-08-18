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
            current_tip = get_block_tip(gql_url, config.headless_jwt_secret)
            
            existing_block = sess.scalar(
                select(Block).where(
                    Block.planet_id == planet_id.encode(),
                    Block.pass_type == PassType.WORLD_CLEAR_PASS,
                )
            )
            
            if not existing_block:
                raise ValueError(f"No existing block found for planet {planet_id} and pass type {PassType.WORLD_CLEAR_PASS}")
            
            start_from = existing_block.last_processed_index + 1
            
            if start_from >= current_tip:
                logger.info(
                    f"Planet {planet_id}: Already up to date. Current tip: {current_tip}"
                )
                continue
                
            logger.info(
                f"Processing blocks from {start_from} to {current_tip}",
                tracker="world_clear_tracker",
                planet_id=planet_id,
                start_block=start_from,
                end_block=current_tip,
            )
            
            # Process blocks in batches of 100
            end_block = min(start_from + 100, current_tip)
            
            for block_index in range(start_from, end_block):
                try:
                    track_world_clear_actions(planet_id, gql_url, block_index)
                    
                    if existing_block:
                        existing_block.last_processed_index = block_index
                    else:
                        existing_block = Block(
                            planet_id=planet_id.encode(),
                            pass_type=PassType.WORLD_CLEAR_PASS,
                            last_processed_index=block_index,
                        )
                        sess.add(existing_block)
                    
                    sess.commit()
                    logger.info(f"Block {block_index} processed successfully")
                    
                except Exception as e:
                    sess.rollback()
                    logger.exception(
                        f"Error processing block {block_index}",
                        exc=e,
                    )
                    break
            
            logger.info(
                f"Processed blocks from {start_from} to {end_block}",
                tracker="world_clear_tracker",
                planet_id=planet_id,
            )
                    
        except Exception as e:
            logger.exception(
                f"Error in track_missing_blocks for planet {planet_id}",
                exc=e,
            )
        finally:
            sess.close()
