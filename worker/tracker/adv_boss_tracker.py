import concurrent.futures
import json
import os
from collections import defaultdict

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker, scoped_session

from common import logger
from common.enums import PlanetID, PassType
from common.models.action import Block
from common.utils.aws import fetch_secrets
from schemas.action import AdventureBossActionJson
from utils.aws import send_sqs_message
from utils.gql import get_block_tip, fetch_block_data

# envs of tracker comes from .env.*** in EC2 instance
REGION_NAME = os.environ.get("REGION_NAME")
GQL_URL = os.environ.get("GQL_URL")
SQS_URL = os.environ.get("ADVENTURE_BOSS_SQS_URL")
CURRENT_PLANET = PlanetID(os.environ.get("PLANET_ID").encode())

DB_URI = os.environ.get("DB_URI")
db_password = fetch_secrets(os.environ.get("REGION_NAME", "us-east-2"), os.environ.get("SECRET_ARN"))["password"]
DB_URI = DB_URI.replace("[DB_PASSWORD]", db_password)

engine = create_engine(DB_URI)


def process_block(block_index: int):
    tx_data, tx_result_list = fetch_block_data(block_index, PassType.ADVENTURE_BOSS_PASS)

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


def main():
    sess = scoped_session(sessionmaker(bind=engine))
    # Get missing blocks
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
    missing_blocks = expected_all - all_blocks

    block_dict = {}
    with concurrent.futures.ThreadPoolExecutor() as executor:
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


if __name__ == "__main__":
    main()
