import asyncio
import json
import os
import re
import urllib.parse
from collections import defaultdict
from typing import List

import bencodex
import requests
from sqlalchemy import create_engine
from sqlalchemy.orm import scoped_session, sessionmaker

from block_tracker import get_deposit, send_message
from common import logger
from common.enums import PlanetID
from common.models.action import Block
from common.utils.aws import fetch_secrets
from utils.stake import StakeAPCoef

DB_URI = os.environ.get("DB_URI")
db_password = fetch_secrets(os.environ.get("REGION_NAME", "us-east-2"), os.environ.get("SECRET_ARN"))["password"]
DB_URI = DB_URI.replace("[DB_PASSWORD]", db_password)
SCAN_URL = os.environ.get("SCAN_URL")
CURRENT_PLANET = PlanetID(bytes(os.environ.get("PLANET_ID", "utf-8")))


class GameAction:
    avatar_address: str
    count_base: int

    def __init__(self, raw_action: dict):
        type_id = raw_action['type_id']
        values = raw_action['values']
        if type_id.startswith('raid'):
            avatar_address = values['a'].hex()
            count_base = 1
        elif type_id.startswith('hack_and_slash'):
            if "avatarAddress" not in values:
                logger.info(values)
            avatar_address = values['avatarAddress'].hex()
            if 'sweep' in type_id:
                count_base = int(values['actionPoint']) + int(values['apStoneCount']) * 120
            else:
                count_base = int(values['totalPlayCount'])
        elif type_id.startswith('battle_arena'):
            avatar_address = values['maa'].hex()
            count_base = int(values['tk'])
        if not avatar_address.startswith('0x'):
            avatar_address = f'0x{avatar_address}'
        self.avatar_address = avatar_address.lower()
        self.count_base = count_base


async def fetch_txs():
    engine = create_engine(DB_URI)
    sess = scoped_session(sessionmaker(bind=engine))
    min_inde = (sess.query(Block.index)
                 .filter(Block.planet_id == CURRENT_PLANET)
                 .order_by(Block.index.desc())
                 ).first()[0]
    block_url = urllib.parse.urljoin(SCAN_URL, 'blocks')
    resp = requests.get(block_url, {'limit': 1})
    blocks = resp.json()['blocks']
    block = blocks[0]
    limit = block['index']
    index_range = [i for i in range(min_index, limit)]
    exist_index = [i[0] for i in sess.query(Block.index).where(
        Block.index.in_(index_range)
    )]
    target_indexes = [i for i in index_range if i not in exist_index]
    for index, i in enumerate(target_indexes):
        total_count = len(target_indexes)
        await save_tx(index, i, total_count)


async def save_tx(index, block_index, total_count):
    action_types = r"(hack_and_slash.*)|(battle_arena.*)|(raid.*)"
    regex = re.compile(action_types)
    logger.info(f'run fetch txs in block {block_index} : {index}/{total_count}')
    url = urllib.parse.urljoin(SCAN_URL, f'blocks/{block_index}')
    resp = requests.get(url)
    block_data = resp.json()
    transactions = block_data['transactions']
    synced_txs = {}
    file_name = f'/tmp/block_data/block_{block_index}_txs.json'
    if os.path.exists(file_name):
        with open(file_name, 'r') as f:
            synced_txs = json.load(f)
            logger.info(f'load fetched txs {file_name}({len(synced_txs)})')
    for transaction in transactions:
        logger.info(transaction["id"])
        action = transaction['actions'][0]
        action_type = action['typeId']
        if regex.match(action_type) and 'random_buff' not in action_type:
            tx_id = transaction['id']
            if synced_txs.get(tx_id) is not None:
                continue
            des = bencodex.loads(bytes.fromhex(action['raw']))
            ga = GameAction(des)
            synced_txs[transaction['id']] = {
                "agent_addr": transaction['signer'].lower(),
                "avatar_addr": ga.avatar_address,
                "count_base": ga.count_base,
                "result": None,
                "action_type": action_type,
            }
    with open(file_name, 'w') as f:
        json.dump(synced_txs, f)
    logger.info(f'save fetched txs {file_name}({len(synced_txs)})')
    await update_tx(index, block_index, total_count)
    data = prepare_action_data(block_index, block_index + 1)
    queue_action_data(data)
    deposit_file_name = f'/tmp/block_data/block_{block_index}_deposit.json'
    success_file_name = f'/tmp/block_data/block_{block_index}_success_txs.json'
    for file_path in [file_name, deposit_file_name, success_file_name]:
        if os.path.exists(file_path):
            os.remove(file_path)
            logger.info(f'delete staged block {file_path}')

    return data


async def update_tx(index, block_index, total_count):
    url = os.environ.get("GQL_URL")
    query = """
        query($txId: TxId!) {
      transaction {
        transactionResult(txId: $txId) {
          txStatus
        }
      }
    }
    """
    coef = StakeAPCoef(url)
    logger.info(f'run fetch tx result({block_index}) {index}/{total_count}')
    file_name = f'/tmp/block_data/block_{block_index}_txs.json'
    deposit_file_name = f'/tmp/block_data/block_{block_index}_deposit.json'
    if not os.path.exists(file_name):
        logger.info(f'skip fetch tx result {file_name}/{total_count}')
    else:
        synced_deposit = {}
        if os.path.exists(deposit_file_name):
            with open(deposit_file_name, 'r') as f:
                synced_deposit = json.load(f)
                logger.info(f'load deposit {deposit_file_name}({len(synced_deposit)})')
        with open(file_name, 'r') as f:
            synced_txs = json.load(f)
        success_file_name = f'/tmp/block_data/block_{block_index}_success_txs.json'
        synced_success_txs = None
        if os.path.exists(success_file_name):
            with open(success_file_name, 'r') as f:
                synced_success_txs = json.load(f)
                logger.info(f'load success txs {success_file_name}({len(synced_success_txs)})')
        logger.info(f'start get({block_index}) tx result from node')
        await get_tx_results(url, synced_txs, synced_deposit, coef, synced_success_txs)
        logger.info(f'finish get({block_index} tx result from node')
        before = len(synced_txs)
        success_txs = {}
        if synced_success_txs is not None:
            success_txs = synced_success_txs
        for k in synced_txs:
            tx = synced_txs[k]
            if tx['result'] == "SUCCESS":
                success_txs[k] = tx
        logger.info(f'filter success txs ({len(success_txs)}/{before})')
        with open(success_file_name, 'w') as f:
            json.dump(success_txs, f)
            logger.info(f'save txs {success_file_name}({len(success_txs)})')
        with open(deposit_file_name, 'w') as f:
            json.dump(synced_deposit, f)
            logger.info(f'save deposit {deposit_file_name}({len(synced_deposit)})')


async def get_tx_result(url, query, k, tx, synced_deposit, coef, synced_txs):
    result = {}
    signer = tx['agent_addr']
    if synced_txs is not None and synced_txs.get(k) is not None:
        tx['result'] = synced_txs['result']
    else:
        resp = requests.post(url, json={'query': query, 'variables': {'txId': k}})
        data = resp.json()["data"]["transaction"]["transactionResult"]["txStatus"]
        tx['result'] = data
    if synced_deposit.get(signer) is None:
        get_deposit(coef, url, result, signer)
        synced_deposit[signer] = result[signer]


async def get_tx_results(url, txs, synced_deposit, coef, synced_txs):
    query = """
query($txIds: [TxId]!) {
  transaction {
    transactionResults(txIds: $txIds) {
      txStatus
    }
  }
}
    """
    result = {}
    tx_ids = []
    signers = []
    for k in txs:
        tx = txs[k]
        signer = tx['agent_addr']
        if synced_txs is not None and synced_txs.get(k) is not None:
            tx['result'] = synced_txs[k]['result']
        else:
            tx_ids.append(k)
        if synced_deposit.get(signer) is None:
            signers.append(signer)
    if len(tx_ids) > 0:
        resp = requests.post(url, json={'query': query, 'variables': {'txIds': tx_ids}})
        data = resp.json()["data"]["transaction"]["transactionResults"]
        for i, tx_id in enumerate(tx_ids):
            txs[tx_id]['result'] = data[i]["txStatus"]
    if len(signers) > 0:
        get_deposits(coef, url, result, signers)
        for signer in signers:
            synced_deposit[signer] = result[signer]


def prepare_action_data(min_index: int, limit: int):
    action_data = defaultdict(lambda: defaultdict(list))
    for index, i in enumerate(range(min_index, limit)):
        file_name = f'/tmp/block_data/block_{i}_success_txs.json'
        target_file_name = f'/tmp/block_data/block_{i}_action_data.json'
        if not os.path.exists(file_name):
            logger.info(f'skip target index {i}')
            continue
        with open(file_name, 'r') as f:
            txs = json.load(f)
        for tx_id in txs:
            tx = txs[tx_id]
            action_type = tx['action_type']
            avatar_address = tx['avatar_addr']
            if not avatar_address.startswith('0x'):
                avatar_address = f'0x{avatar_address}'
            action_data[i][action_type].append({
                'agent_addr': tx['agent_addr'],
                'avatar_addr': avatar_address,
                'count_base': tx['count_base'],
            })
    return action_data


def queue_action_data(action_data: defaultdict):
    for i in action_data:
        deposit_file_name = f'/tmp/block_data/block_{i}_deposit.json'
        with open(deposit_file_name, 'r') as f:
            stake_data = json.load(f)
        send_message(i, action_data[i], stake_data)
        logger.info(f'send message {i} block')


def get_deposits(coef: StakeAPCoef, url: str, result: dict, addresses: List[str]):
    query = """
query($addresses: [Address]!) {
  stateQuery {
    stakeStates(addresses: $addresses) {
      deposit
    }
  }
}
"""
    resp = requests.post(url, json={"query": query, 'variables': {'addresses': addresses}})
    data = resp.json()["data"]["stateQuery"]["stakeStates"]
    for i, address in enumerate(addresses):
        if data[i] is None:
            stake_amount = 0
        else:
            stake_amount = float(data[i]["deposit"])
        result[address.lower()] = coef.get_ap_coef(stake_amount)


def scrap_block(event, context):
    if not os.path.exists("/tmp/block_data"):
        os.makedirs("tmp/block_data")

    loop = asyncio.get_event_loop()
    loop.run_until_complete(fetch_txs())
    loop.close()


if __name__ == "__main__":
    if not os.path.exists("/tmp/block_data"):
        os.makedirs("tmp/block_data")

    loop = asyncio.get_event_loop()
    loop.run_until_complete(fetch_txs())
    loop.close()
