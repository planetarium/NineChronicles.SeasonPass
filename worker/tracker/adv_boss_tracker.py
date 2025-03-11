import concurrent.futures
import json
import os
import time
from collections import defaultdict

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker, scoped_session

from common import logger
from common.enums import PlanetID, PassType
from common.models.action import Block
from worker.schemas.action import AdventureBossActionJson
from worker.utils.gql import get_block_tip, fetch_block_data

# envs of tracker comes from .env.*** in EC2 instance
REGION_NAME = os.environ.get("REGION_NAME")
GQL_URL = os.environ.get("GQL_URL")
CURRENT_PLANET = PlanetID(os.environ.get("PLANET_ID").encode())

DB_URI = os.environ.get("DB_URI")

engine = create_engine(DB_URI)


def process_block(block_index: int):
    tx_data, tx_result_list = fetch_block_data(
        block_index, PassType.ADVENTURE_BOSS_PASS
    )
    # Add a small sleep after GraphQL query
    time.sleep(0.1)

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

    # Directly call the handler
    event = {
        "Records": [
            {
                "messageId": f"direct-call-{block_index}",
                "body": {
                    "planet_id": CURRENT_PLANET.value.decode(),
                    "block": block_index,
                    "action_data": dict(action_data),
                },
            }
        ]
    }
    handle(event, None)
    return action_data


def main():
    while True:
        sess = scoped_session(sessionmaker(bind=engine))
        # Get missing blocks
        start_block = int(os.environ.get("START_BLOCK_INDEX"))
        expected_all = set(
            range(start_block, get_block_tip())
        )  # Sloth needs 1 block to render actions: get tip-1
        all_blocks = set(
            sess.scalars(
                select(Block.index).where(
                    Block.planet_id == CURRENT_PLANET,
                    Block.index >= start_block,
                    Block.pass_type == PassType.ADVENTURE_BOSS_PASS,
                )
            ).fetchall()
        )
        missing_blocks = expected_all - all_blocks
        sess.close()

        block_dict = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            for index in missing_blocks:
                block_dict[executor.submit(process_block, index)] = index

            for future in concurrent.futures.as_completed(block_dict):
                index = block_dict[future]
                exc = future.exception()

                if exc:
                    logger.error(f"Error occurred processing block {index} :: {exc}")
                else:
                    result = future.result()
                    logger.info(f"Block {index} collected :: {result}")
        # wait for block time
        time.sleep(8)


if __name__ == "__main__":
    main()
