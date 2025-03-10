import concurrent.futures
import json
import os
import time
import asyncio
from collections import defaultdict

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker, scoped_session

from common import logger
from common.enums import PlanetID, PassType
from common.models.action import Block
from worker.schemas.action import AdventureBossActionJson
from worker.utils.aws import send_sqs_message
from worker.utils.gql import get_block_tip, fetch_block_data_async

# envs of tracker comes from .env.*** in EC2 instance
REGION_NAME = os.environ.get("REGION_NAME")
GQL_URL = os.environ.get("GQL_URL")
SQS_URL = os.environ.get("ADVENTURE_BOSS_SQS_URL")
CURRENT_PLANET = PlanetID(os.environ.get("PLANET_ID").encode())
MAX_WORKERS = int(os.environ.get("MAX_WORKERS", "10"))

DB_URI = os.environ.get("DB_URI")

engine = create_engine(DB_URI)


async def process_block(block_index: int):
    tx_data, tx_result_list = await fetch_block_data_async(block_index, PassType.ADVENTURE_BOSS_PASS)

    action_data = defaultdict(list)
    for i, tx in enumerate(tx_data):
        if tx_result_list[i] != "SUCCESS":
            continue

        for action in tx["actions"]:
            action_raw = json.loads(action["json"].replace(r"\uFEFF", ""))
            type_id = action_raw["type_id"]

            action_json = AdventureBossActionJson(type_id=type_id, **(action_raw["values"]))
            action_data[action_json.type_id].append({
                "tx_id": tx["id"],
                "season_index": action_json.season_index,
                "agent_addr": tx["signer"].lower(),
                "avatar_addr": action_json.avatar_addr.lower(),
                "count_base": action_json.count_base,
            })

    send_sqs_message(REGION_NAME, CURRENT_PLANET, SQS_URL, block_index, action_data)
    return action_data

async def main():
    while True:
        sess = scoped_session(sessionmaker(bind=engine))
        start_block = int(os.environ.get("START_BLOCK_INDEX"))
        expected_all = set(range(start_block, get_block_tip()))  # Sloth needs 1 block to render actions: get tip-1
        all_blocks = set(sess.scalars(
            select(Block.index)
            .where(
                Block.planet_id == CURRENT_PLANET,
                Block.index >= start_block,
                Block.pass_type == PassType.ADVENTURE_BOSS_PASS
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
