import hashlib
import hmac
import json
import os
import time
import asyncio
import logging

import bencodex
import eth_utils
import requests
import aiohttp

from common.enums import PassType, PlanetID
from common.utils._graphql import GQL_DICT
from common.utils.season_pass import create_jwt_token

GQL_URL = os.environ.get("GQL_URL")
TARGET_ACTION_DICT = {
    PassType.COURAGE_PASS: "(hack_and_slash.*)|(battle.*)|(raid.*)|(event_dungeon_battle.*)",
    PassType.ADVENTURE_BOSS_PASS: "(wanted.*)|(explore_adventure_boss.*)|(sweep_adventure_boss.*)",
    PassType.WORLD_CLEAR_PASS: "(hack_and_slash.*)"
}

logger = logging.getLogger(__name__)


def checksum_encode(addr: bytes) -> str:  # Takes a 20-byte binary address as input
    """
    Convert input address to checksum encoded address without prefix "0x"
    See [ERC-55](https://eips.ethereum.org/EIPS/eip-55)

    :param addr: 20-bytes binary address
    :return: checksum encoded address as string
    """
    hex_addr = addr.hex()
    checksum_buffer = ""

    # Treat the hex address as ascii/utf-8 for keccak256 hashing
    hashed_address = eth_utils.keccak(text=hex_addr).hex()

    # Iterate over each character in the hex address
    for nibble_index, character in enumerate(hex_addr):
        if character in "0123456789":
            # We can't upper-case the decimal digits
            checksum_buffer += character
        elif character in "abcdef":
            # Check if the corresponding hex digit (nibble) in the hash is 8 or higher
            hashed_address_nibble = int(hashed_address[nibble_index], 16)
            if hashed_address_nibble > 7:
                checksum_buffer += character.upper()
            else:
                checksum_buffer += character
        else:
            raise eth_utils.ValidationError(
                f"Unrecognized hex character {character!r} at position {nibble_index}"
            )
    return checksum_buffer


def derive_address(addr: str, key: str) -> str:
    raw = bytes.fromhex(addr[2:] if addr.startswith("0x") else addr)
    return checksum_encode(hmac.new(
        key.encode("utf-8"),
        raw,
        digestmod=hashlib.sha1
    ).digest())


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


async def fetch_block_data_async(block_index: int, pass_type: PassType):
    """
    Asynchronously fetch block data using aiohttp.
    
    Args:
        block_index: The block index to fetch
        pass_type: The type of pass to filter actions
        
    Returns:
        Tuple of transaction data and transaction result list
    """
    auth_header = {"Authorization": f"Bearer {create_jwt_token(os.environ.get('HEADLESS_GQL_JWT_SECRET'))}"}
    
    try:
        nct_query = f"""{{ transaction {{ ncTransactions (
            startingBlockIndex: {block_index},
            limit: 1,
            actionType: "{TARGET_ACTION_DICT[pass_type]}"
        ) {{ id signer actions {{ json }} }}
        }} }}"""
        
        async with aiohttp.ClientSession() as session:
            async with session.post(
                GQL_URL,
                json={"query": nct_query},
                headers=auth_header
            ) as response:
                if response.status != 200:
                    logger.error(f"GQL API error: HTTP {response.status} while fetching transactions for block {block_index}")
                    return None, None
                
                resp_data = await response.json()
                if not resp_data or 'data' not in resp_data or 'transaction' not in resp_data['data'] or 'ncTransactions' not in resp_data['data']['transaction']:
                    logger.error(f"Invalid GQL response for transactions in block {block_index}: {resp_data}")
                    return None, None
                
                tx_data = resp_data["data"]["transaction"]["ncTransactions"]
                if not tx_data:
                    logger.info(f"No transactions found for block {block_index}")
                    return [], []

                tx_id_list = [x["id"] for x in tx_data]

                if not tx_id_list:
                    logger.info(f"No transaction IDs found for block {block_index}")
                    return [], []

                tx_result_query = f"""{{ transaction {{ transactionResults (txIds: {json.dumps(tx_id_list)}) {{ txStatus }} }} }}"""
                
                async with session.post(
                    GQL_URL,
                    json={"query": tx_result_query},
                    headers=auth_header
                ) as result_response:
                    if result_response.status != 200:
                        logger.error(f"GQL API error: HTTP {result_response.status} while fetching transaction results for block {block_index}")
                        return None, None
                    
                    resp_result = await result_response.json()
                    if not resp_result or 'data' not in resp_result or 'transaction' not in resp_result['data'] or 'transactionResults' not in resp_result['data']['transaction']:
                        logger.error(f"Invalid GQL response for transaction results in block {block_index}: {resp_result}")
                        return None, None
                    
                    tx_result_list = [x["txStatus"] for x in resp_result["data"]["transaction"]["transactionResults"]]
    
    except aiohttp.ClientError as e:
        logger.error(f"Network error during GQL request for block {block_index}: {str(e)}")
        return None, None
    except Exception as e:
        logger.error(f"Unexpected error during GQL request for block {block_index}: {str(e)}")
        return None, None
    
    await asyncio.sleep(0.1)
    
    return tx_data, tx_result_list


def get_explore_floor(planet_id: PlanetID, block_index: int, season: int, avatar_addr: str) -> int:
    season_string = f"{season:040}"
    query = f"""{{ state(
        index: {block_index + 1},  # Sloth needs 1 block to render actions
        accountAddress: "0000000000000000000000000000000000000102",  # Fixed address for exploreBoard
        address: "{derive_address(avatar_addr, season_string)}"
    ) }}
    """
    resp = requests.post(
        GQL_DICT[planet_id], json={"query": query},
        headers={"Authorization": f"Bearer {create_jwt_token(os.environ.get('HEADLESS_GQL_JWT_SECRET'))}"}
    )
    data = resp.json()["data"]["state"]
    if data is None:
        return 0
    return bencodex.loads(bytes.fromhex(data))[3]
