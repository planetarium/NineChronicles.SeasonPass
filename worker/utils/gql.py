import json
import os

import requests

from common.enums import PassType
from common.utils.season_pass import create_jwt_token


GQL_URL = os.environ.get("GQL_URL")
TARGET_ACTION_DICT = {
    PassType.COURAGE_PASS: "(hack_and_slash.*)|(battle_arena.*)|(raid.*)|(event_dungeon_battle.*)",
    PassType.ADVENTURE_BOSS_PASS: "(wanted.*)|(explore_adventure_boss.*)|(sweep_adventure_boss.*)",
    PassType.WORLD_CLEAR_PASS: "(hack_and_slask.*)"
}


def get_block_tip():
    resp = requests.post(
        GQL_URL,
        json={"query": "{ nodeStatus { tip { index } } }"},
        headers={"Authorization": f"Bearer {create_jwt_token(os.environ.get('HEADLESS_GQL_JWT_SECRET'))}"}
    )
    return resp.json()["data"]["nodeStatus"]["tip"]["index"]


def fetch_block_data(block_index: int, pass_type: PassType):
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
    return tx_data, tx_result_list
