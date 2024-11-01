import concurrent.futures
import json
import os
from collections import defaultdict

import boto3
import requests
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker, scoped_session

from common import logger
from common.enums import PlanetID, PassType
from common.models.action import Block
from common.utils.aws import fetch_secrets
from common.utils.season_pass import create_jwt_token
from schemas.action import ActionJson
from utils.stake import StakeAPCoef
from worker.utils.aws import send_sqs_message
from worker.utils.gql import get_block_tip

GQL_URL = os.environ.get("GQL_URL")
CURRENT_PLANET = PlanetID(os.environ.get("PLANET_ID").encode())
DB_URI = os.environ.get("DB_URI")
db_password = fetch_secrets(os.environ.get("REGION_NAME", "us-east-2"), os.environ.get("SECRET_ARN"))["password"]
DB_URI = DB_URI.replace("[DB_PASSWORD]", db_password)
SQS_URL = os.environ.get("SQS_URL")

TARGET_ACTION_DICT = {
    PassType.COURAGE_PASS: "(hack_and_slash.*)|(battle_arena.*)|(raid.*)|(event_dungeon_battle.*)",
    PassType.ADVENTURE_BOSS_PASS: "(wanted.*)|(explore_adventure_boss.*)|(sweep_adventure_boss.*)",
    PassType.WORLD_CLEAR_PASS: "(hack_and_slask.*)"
}

engine = create_engine(DB_URI)


def get_deposit(coef: StakeAPCoef, addr: str) -> float:
    query = f'{{ stateQuery {{ stakeState(address: "{addr}") {{ deposit }} }} }}'
    resp = requests.post(
        GQL_URL,
        json={"query": query},
        headers={"Authorization": f"Bearer {create_jwt_token(os.environ.get('HEADLESS_GQL_JWT_SECRET'))}"}
    )
    data = resp.json()["data"]["stateQuery"]["stakeState"]
    if data is None:
        stake_amount = 0.
    else:
        stake_amount = float(data["deposit"])

    return coef.get_ap_coef(stake_amount)


def process_block(block_index: int, pass_type: PassType):
    # Fetch Tx. and actions
    nct_query = f"""{{ transaction {{ ncTransactions (
        startingBlockIndex: {block_index},
        limit: 1,
        actionType: "{TARGET_ACTION_DICT[pass_type]}"
    ) {{ id signer actions {{ json }} }}
    }} }}"""
    resp = requests.post(
        GQL_URL,
        json={"query": nct_query},
        headers={"Authorization": f"Bearer {create_jwt_token(os.environ.get('HEADLESS_GQL_JWT_SECRET'))}"}
    )
    tx_data = resp.json()["data"]["transaction"]["ncTransactions"]

    tx_id_list = [x["id"] for x in tx_data]

    # Fetch Tx. results
    tx_result_query = f"""{{ transaction {{ transactionResults (txIds: {json.dumps(tx_id_list)}) {{ txStatus }} }} }}"""
    resp = requests.post(
        GQL_URL,
        json={"query": tx_result_query},
        headers={"Authorization": f"Bearer {create_jwt_token(os.environ.get('HEADLESS_GQL_JWT_SECRET'))}"}
    )
    tx_result_list = [x["txStatus"] for x in resp.json()["data"]["transaction"]["transactionResults"]]

    action_data = defaultdict(list)
    agent_list = set()
    for i, tx in enumerate(tx_data):
        if tx_result_list[i] != "SUCCESS":
            continue

        for action in tx["actions"]:
            action_raw = json.loads(action["json"].replace(r"\uFEFF", ""))
            type_id = action_raw["type_id"]
            if "random_buff" in type_id:  # hack_and_slash_random_buff
                continue
            if "claim" in type_id:  # claim_raid_reward
                continue
            if "raid_reward" in type_id:
                continue

            agent_list.add(tx["signer"].lower())
            action_json = ActionJson(type_id=type_id, **(action_raw["values"]))
            action_data[action_json.type_id].append({
                "tx_id": tx["id"],
                "agent_addr": tx["signer"].lower(),
                "avatar_addr": action_json.avatar_addr.lower(),
                "count_base": action_json.count_base,
            })

    send_sqs_message(CURRENT_PLANET, SQS_URL, block_index, action_data)


def main():
    sess = scoped_session(sessionmaker(bind=engine))
    # Get missing blocks
    expected_all = set(range(int(os.environ.get("START_BLOCK_INDEX")), get_block_tip(GQL_URL) + 1))
    all_blocks = set(sess.scalars(select(Block.index).where(Block.planet_id == CURRENT_PLANET)).fetchall())
    missing_blocks = expected_all - all_blocks

    block_dict = {}
    with concurrent.futures.ThreadPoolExecutor() as executor:
        for index in missing_blocks:
            block_dict[executor.submit(process_block, index, PassType.COURAGE_PASS)] = (index, PassType.COURAGE_PASS)
            block_dict[executor.submit(process_block, index, PassType.ADVENTURE_BOSS_PASS)] = (index, PassType.ADVENTURE_BOSS_PASS)
            block_dict[executor.submit(process_block, index, PassType.WORLD_CLEAR_PASS)] = (index, PassType.WORLD_CLEAR_PASS)

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
