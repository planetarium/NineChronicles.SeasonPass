import concurrent.futures
import json
import os
from collections import defaultdict
from typing import Dict

import boto3
import requests
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker, scoped_session

from common import logger
from common.enums import PlanetID
from common.models.action import Block
from common.utils.aws import fetch_secrets
from schemas.action import ActionJson
from utils.stake import StakeAPCoef

GQL_URL = os.environ.get("GQL_URL")
CURRENT_PLANET = PlanetID(os.environ.get("PLANET_ID").encode())
DB_URI = os.environ.get("DB_URI")
db_password = fetch_secrets(os.environ.get("REGION_NAME", "us-east-2"), os.environ.get("SECRET_ARN"))["password"]
DB_URI = DB_URI.replace("[DB_PASSWORD]", db_password)

engine = create_engine(DB_URI)


def get_deposit(coef: StakeAPCoef, addr: str) -> float:
    query = f'{{ stateQuery {{ stakeState(address: "{addr}") {{ deposit }} }} }}'
    resp = requests.post(GQL_URL, json={"query": query})
    data = resp.json()["data"]["stateQuery"]["stakeState"]
    if data is None:
        stake_amount = 0.
    else:
        stake_amount = float(data["deposit"])

    return coef.get_ap_coef(stake_amount)


def send_message(index: int, action_data: defaultdict, stake_data: Dict[str, int]):
    sqs = boto3.client("sqs", region_name=os.environ.get("REGION_NAME"))
    message = {
        "planet_id": CURRENT_PLANET.value.decode(),
        "block": index,
        "action_data": dict(action_data),
        "stake": dict(stake_data),
    }
    resp = sqs.send_message(
        QueueUrl=os.environ.get("SQS_URL"),
        MessageBody=json.dumps(message),
    )
    logger.info(f"Message {resp['MessageId']} sent to SQS for block {index}.")


def get_block_tip() -> int:
    try:
        # Use 9cscan
        resp = requests.get(os.environ.get("SCAN_URL"))
        if resp.status_code == 200:
            return resp.json()["blocks"][0]["index"]
        else:
            # Use GQL for fail over
            resp = requests.post(os.environ.get("GQL_URL"), json={"query": "{ nodeStatus { tip { index } } }"})
            return resp.json()["data"]["nodeStatus"]["tip"]["index"]
    except:
        # Use GQL for fail over
        resp = requests.post(os.environ.get("GQL_URL"), json={"query": "{ nodeStatus { tip { index } } }"})
        return resp.json()["data"]["nodeStatus"]["tip"]["index"]


def process_block(coef: StakeAPCoef, block_index: int):
    # Fetch Tx. and actions
    nct_query = f"""{{ transaction {{ ncTransactions (
        startingBlockIndex: {block_index},
        limit: 1,
        actionType: "(hack_and_slash.*)|(battle_arena.*)|(raid.*)|(event_dungeon_battle.*)"
    ) {{ id signer actions {{ json }} }}
    }} }}"""
    resp = requests.post(GQL_URL, json={"query": nct_query})
    tx_data = resp.json()["data"]["transaction"]["ncTransactions"]

    tx_id_list = [x["id"] for x in tx_data]

    # Fetch Tx. results
    tx_result_query = f"""{{ transaction {{ transactionResults (txIds: {json.dumps(tx_id_list)}) {{ txStatus }} }} }}"""
    resp = requests.post(GQL_URL, json={"query": tx_result_query})
    tx_result_list = [x["txStatus"] for x in resp.json()["data"]["transaction"]["transactionResults"]]

    action_data = defaultdict(list)
    agent_list = set()
    for i, tx in enumerate(tx_data):
        if tx_result_list[i] != "SUCCESS":
            continue

        for action in tx["actions"]:
            action_raw = json.loads(action["json"].replace(r"\uFEFF", ""))
            type_id = action_raw["type_id"]
            if "random_buff" in type_id:
                continue

            agent_list.add(tx["signer"].lower())
            action_json = ActionJson(type_id=type_id, **(action_raw["values"]))
            action_data[action_json.type_id].append({
                "tx_id": tx["id"],
                "agent_addr": tx["signer"].lower(),
                "avatar_addr": action_json.avatar_addr.lower(),
                "count_base": action_json.count_base,
            })

    # Fetch stake states
    stake_data = {}
    stake_dict = {}
    with concurrent.futures.ThreadPoolExecutor() as executor:
        for agent in agent_list:
            stake_dict[executor.submit(get_deposit, coef, agent)] = agent

        for future in concurrent.futures.as_completed(stake_dict):
            stake_data[agent] = future.result()

    send_message(block_index, action_data, stake_data)


def main():
    coef = StakeAPCoef(GQL_URL)

    sess = scoped_session(sessionmaker(bind=engine))
    # Get missing blocks
    expected_all = set(range(int(os.environ.get("START_BLOCK_INDEX")), get_block_tip() + 1))
    all_blocks = set(sess.scalars(select(Block.index).where(Block.planet_id == CURRENT_PLANET)).fetchall())
    missing_blocks = expected_all - all_blocks

    block_dict = {}
    with concurrent.futures.ThreadPoolExecutor() as executor:
        for index in missing_blocks:
            block_dict[executor.submit(process_block, coef, index)] = index

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
