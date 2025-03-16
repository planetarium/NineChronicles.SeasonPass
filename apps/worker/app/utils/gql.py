import hashlib
import hmac
import json
import os
from typing import Optional

import bencodex
import eth_utils
import requests

from shared.enums import PassType
from shared.utils.season_pass import create_jwt_token

TARGET_ACTION_DICT = {
    PassType.COURAGE_PASS: "(hack_and_slash.*)|(battle.*)|(raid.*)|(event_dungeon_battle.*)",
    PassType.ADVENTURE_BOSS_PASS: "(wanted.*)|(explore_adventure_boss.*)|(sweep_adventure_boss.*)",
    PassType.WORLD_CLEAR_PASS: "(hack_and_slash.*)",
}


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
    return checksum_encode(
        hmac.new(key.encode("utf-8"), raw, digestmod=hashlib.sha1).digest()
    )


def get_block_tip(gql_url: str, headless_jwt_secret: Optional[str] = None):
    resp = requests.post(
        gql_url,
        json={"query": "{ nodeStatus { tip { index } } }"},
        headers={"Authorization": f"Bearer {create_jwt_token(headless_jwt_secret)}"},
    )
    return resp.json()["data"]["nodeStatus"]["tip"]["index"]


def fetch_block_data(
    gql_url: str,
    block_index: int,
    pass_type: PassType,
    headless_jwt_secret: Optional[str] = None,
):
    # Fetch Tx. and actions
    nct_query = f"""{{ transaction {{ ncTransactions (
        startingBlockIndex: {block_index},
        limit: 1,
        actionType: "{TARGET_ACTION_DICT[pass_type]}"
    ) {{ id signer actions {{ json }} }}
    }} }}"""
    resp = requests.post(
        gql_url,
        json={"query": nct_query},
        headers={"Authorization": f"Bearer {create_jwt_token(headless_jwt_secret)}"},
    )
    tx_data = resp.json()["data"]["transaction"]["ncTransactions"]

    tx_id_list = [x["id"] for x in tx_data]

    # Fetch Tx. results
    tx_result_query = f"""{{ transaction {{ transactionResults (txIds: {json.dumps(tx_id_list)}) {{ txStatus }} }} }}"""
    resp = requests.post(
        gql_url,
        json={"query": tx_result_query},
        headers={"Authorization": f"Bearer {create_jwt_token(headless_jwt_secret)}"},
    )
    tx_result_list = [
        x["txStatus"] for x in resp.json()["data"]["transaction"]["transactionResults"]
    ]
    return tx_data, tx_result_list


def get_explore_floor(
    gql_url: str,
    block_index: int,
    season: int,
    avatar_addr: str,
    headless_jwt_secret: Optional[str] = None,
) -> int:
    season_string = f"{season:040}"
    query = f"""{{ state(
        index: {block_index + 1},  # Sloth needs 1 block to render actions
        accountAddress: "0000000000000000000000000000000000000102",  # Fixed address for exploreBoard
        address: "{derive_address(avatar_addr, season_string)}"
    ) }}
    """
    resp = requests.post(
        gql_url,
        json={"query": query},
        headers={"Authorization": f"Bearer {create_jwt_token(headless_jwt_secret)}"},
    )
    data = resp.json()["data"]["state"]
    if data is None:
        return 0
    return bencodex.loads(bytes.fromhex(data))[3]
