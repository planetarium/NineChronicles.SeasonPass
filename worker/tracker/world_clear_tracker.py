import concurrent.futures
import json
import os
import time
import asyncio
from collections import defaultdict

import requests
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker, scoped_session

from common import logger
from common.enums import PlanetID, PassType
from common.models.action import Block
from common.utils.season_pass import create_jwt_token
from worker.schemas.action import ActionJson
from worker.utils.aws import send_sqs_message
from worker.utils.gql import get_block_tip, fetch_block_data_async

REGION = os.environ.get("REGION_NAME")
GQL_URL = os.environ.get("GQL_URL")
SQS_URL = os.environ.get("WORLD_CLEAR_SQS_URL")
CURRENT_PLANET = PlanetID(os.environ.get("PLANET_ID").encode())
MAX_WORKERS = int(os.environ.get("MAX_WORKERS", "10"))
DB_URI = os.environ.get("DB_URI")

engine = create_engine(DB_URI)


async def process_block(block_index: int):
    tx_data, tx_result_list = await fetch_block_data_async(block_index, PassType.WORLD_CLEAR_PASS)

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
            action_data[action_json.type_id].append({
                "tx_id": tx["id"],
                "agent_addr": tx["signer"].lower(),
                "avatar_addr": action_json.avatar_addr.lower(),
                "world_id": action_json.worldId,
                "stage_id": action_json.stageId,
            })

    send_sqs_message(REGION, CURRENT_PLANET, SQS_URL, block_index, action_data)
    return action_data


async def main():
    while True:
        sess = scoped_session(sessionmaker(bind=engine))
        start_block = int(os.environ.get("START_BLOCK_INDEX"))
        expected_all = set(range(start_block, get_block_tip()))
        all_blocks = set(sess.scalars(
            select(Block.index)
            .where(
                Block.planet_id == CURRENT_PLANET,
                Block.pass_type == PassType.WORLD_CLEAR_PASS,
                Block.index >= start_block,
            )
        ).fetchall())
        missing_blocks = list(expected_all - all_blocks)
        sess.close()

        tasks = []
        for index in missing_blocks:
            if len(tasks) >= MAX_WORKERS:
                done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                tasks = list(pending)
                for task in done:
                    try:
                        result = task.result()
                        logger.info(f"Block {task.block_index} collected :: {result}")
                    except Exception as e:
                        logger.error(f"Error occurred processing block {task.block_index} :: {e}")
            
            task = asyncio.create_task(process_block(index))
            task.block_index = index
            tasks.append(task)
        
        if tasks:
            for task in asyncio.as_completed(tasks):
                try:
                    result = await task
                    logger.info(f"Block {task.block_index} collected :: {result}")
                except Exception as e:
                    logger.error(f"Error occurred processing block {task.block_index} :: {e}")
        
        await asyncio.sleep(8)


if __name__ == "__main__":
    asyncio.run(main())
