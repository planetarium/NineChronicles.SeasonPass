import hashlib
import hmac
import json
import re
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


def get_block_tip(
    gql_url: str,
    headless_jwt_secret: Optional[str] = None,
    timeout: Optional[float] = None,
):
    resp = requests.post(
        gql_url,
        json={"query": "{ nodeStatus { tip { index } } }"},
        headers={"Authorization": f"Bearer {create_jwt_token(headless_jwt_secret)}"},
        timeout=timeout,
    )
    return resp.json()["data"]["nodeStatus"]["tip"]["index"]


_HEX = re.compile(r"^(0x)?[0-9a-fA-F]+$")


class GQLQueryError(Exception):
    """GraphQL 응답이 errors 를 담았거나 기대한 data 가 없을 때."""


def query_once(
    gql_url: str,
    query: str,
    headless_jwt_secret: Optional[str] = None,
    timeout: Optional[float] = None,
) -> dict:
    """GraphQL 을 **한 번만** 부른다(재시도 없음). data 를 돌려준다.

    재서명 판단용이다. 판단은 "모름이면 건드리지 않음"이라 재시도보다 회차 시간을 지키는 게 낫다
    (GQLClient 는 재시도 2번 + 처음 한 번 스키마 조회라 느린 노드에서 호출 한 번이 15초를 넘는다).
    HTTP 오류·errors·data 누락은 모두 예외다."""
    headers = {}
    if headless_jwt_secret:
        headers["Authorization"] = f"Bearer {create_jwt_token(headless_jwt_secret)}"
    resp = requests.post(
        gql_url, json={"query": query}, headers=headers, timeout=timeout
    )
    resp.raise_for_status()
    body = resp.json()
    if body.get("errors"):
        raise GQLQueryError(str(body["errors"])[:200])
    data = body.get("data")
    if not data:
        raise GQLQueryError("no data")
    return data


def get_tip_index_once(
    gql_url: str,
    headless_jwt_secret: Optional[str] = None,
    timeout: Optional[float] = None,
) -> int:
    tip = query_once(
        gql_url, "{ nodeStatus { tip { index } } }", headless_jwt_secret, timeout
    )["nodeStatus"]["tip"]["index"]
    if not isinstance(tip, int):
        raise GQLQueryError(f"bad tip {tip!r}")
    return tip


def get_next_tx_nonce_once(
    gql_url: str,
    address: str,
    headless_jwt_secret: Optional[str] = None,
    timeout: Optional[float] = None,
) -> int:
    """노드의 nextTxNonce(블록 nonce + 그 노드 대기열에 이어진 tx 수)."""
    if not _HEX.match(address):
        raise ValueError(f"bad address {address!r}")
    nonce = query_once(
        gql_url,
        f'{{ transaction {{ nextTxNonce(address: "{address}") }} }}',
        headless_jwt_secret,
        timeout,
    )["transaction"]["nextTxNonce"]
    if not isinstance(nonce, int):
        raise GQLQueryError(f"bad nonce {nonce!r}")
    return nonce


def get_tx_status_once(
    gql_url: str,
    tx_id: str,
    headless_jwt_secret: Optional[str] = None,
    timeout: Optional[float] = None,
) -> str:
    """그 노드 기준 tx 상태 이름(SUCCESS/FAILURE/INCLUDED/STAGING/INVALID)."""
    if not _HEX.match(tx_id):
        raise ValueError(f"bad tx id {tx_id!r}")
    status = query_once(
        gql_url,
        f'{{ transaction {{ transactionResult(txId: "{tx_id}") {{ txStatus }} }} }}',
        headless_jwt_secret,
        timeout,
    )["transaction"]["transactionResult"]["txStatus"]
    if not isinstance(status, str):
        raise GQLQueryError(f"bad status {status!r}")
    return status


def fetch_block_data(
    gql_url: str,
    block_index: int,
    pass_type: PassType,
    headless_jwt_secret: Optional[str] = None,
    timeout: Optional[float] = None,
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
        timeout=timeout,
    )
    tx_data = resp.json()["data"]["transaction"]["ncTransactions"]

    tx_id_list = [x["id"] for x in tx_data]

    # Fetch Tx. results
    tx_result_query = f"""{{ transaction {{ transactionResults (txIds: {json.dumps(tx_id_list)}) {{ txStatus }} }} }}"""
    resp = requests.post(
        gql_url,
        json={"query": tx_result_query},
        headers={"Authorization": f"Bearer {create_jwt_token(headless_jwt_secret)}"},
        timeout=timeout,
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
    timeout: Optional[float] = None,
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
        timeout=timeout,
    )
    data = resp.json()["data"]["state"]
    if data is None:
        return 0
    return bencodex.loads(bytes.fromhex(data))[3]
