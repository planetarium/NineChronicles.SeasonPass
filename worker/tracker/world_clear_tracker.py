import concurrent.futures
import json
import os
import time
from collections import defaultdict

import requests
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker, scoped_session

from common import logger
from common.enums import PlanetID, PassType
from common.models.action import Block
from common.utils.season_pass import create_jwt_token
from worker.schemas.action import ActionJson
from worker.utils.gql import get_block_tip

REGION = os.environ.get("REGION_NAME")
GQL_URL = os.environ.get("GQL_URL")
CURRENT_PLANET = PlanetID(os.environ.get("PLANET_ID").encode())
DB_URI = os.environ.get("DB_URI")

engine = create_engine(DB_URI)


def process_block(block_index: int):
    # Fetch Tx. and actions
    nct_query = f"""{{ transaction {{ ncTransactions (
        startingBlockIndex: {block_index},
        limit: 1,
        actionType: "(hack_and_slash.*)"
    ) {{ id signer actions {{ json }} }}
    }} }}"""
    resp = requests.post(
        GQL_URL,
        json={"query": nct_query},
        headers={
            "Authorization": f"Bearer {create_jwt_token(os.environ.get('HEADLESS_GQL_JWT_SECRET'))}"
        },
    )
    tx_data = resp.json()["data"]["transaction"]["ncTransactions"]

    tx_id_list = [x["id"] for x in tx_data]

    # Fetch Tx. results
    tx_result_query = f"""{{ transaction {{ transactionResults (txIds: {json.dumps(tx_id_list)}) {{ txStatus }} }} }}"""
    resp = requests.post(
        GQL_URL,
        json={"query": tx_result_query},
        headers={
            "Authorization": f"Bearer {create_jwt_token(os.environ.get('HEADLESS_GQL_JWT_SECRET'))}"
        },
    )
    tx_result_list = [
        x["txStatus"] for x in resp.json()["data"]["transaction"]["transactionResults"]
    ]

    # Add a small sleep after GraphQL query
    time.sleep(0.1)

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
                    "world_id": action_json.worldId,
                    "stage_id": action_json.stageId,
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
        expected_all = set(range(start_block, get_block_tip()))
        all_blocks = set(
            sess.scalars(
                select(Block.index).where(
                    Block.planet_id == CURRENT_PLANET,
                    Block.pass_type == PassType.WORLD_CLEAR_PASS,
                    Block.index >= start_block,
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
