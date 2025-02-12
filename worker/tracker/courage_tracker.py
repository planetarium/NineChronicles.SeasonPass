import concurrent.futures
import json
import os
import jwt
import base64
from collections import defaultdict

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker, scoped_session
from sqlalchemy.exc import IntegrityError

from common import logger
from common.enums import PlanetID, PassType
from common.models.action import Block
from common.models.arena import BattleHistory
from common.utils.aws import fetch_secrets
from schemas.action import ActionJson
from utils.aws import send_sqs_message
from utils.gql import get_block_tip, fetch_block_data

REGION = os.environ.get("REGION_NAME")
GQL_URL = os.environ.get("GQL_URL")
CURRENT_PLANET = PlanetID(os.environ.get("PLANET_ID").encode())
DB_URI = os.environ.get("DB_URI")
db_password = fetch_secrets(os.environ.get("REGION_NAME", "us-east-2"), os.environ.get("SECRET_ARN"))["password"]
DB_URI = DB_URI.replace("[DB_PASSWORD]", db_password)
SQS_URL = os.environ.get("SQS_URL")
ARENA_SERVICE_JWT_PUBLIC_KEY = os.environ.get("ARENA_SERVICE_JWT_PUBLIC_KEY")

public_key_pem = base64.b64decode(ARENA_SERVICE_JWT_PUBLIC_KEY).decode("utf-8")

engine = create_engine(DB_URI)

def validate_battle_token(token: str):
    try:
        decoded_token = jwt.decode(
            token,
            public_key_pem,
            algorithms=["RS256"],
            issuer="planetarium arena service",
            audience="NineChronicles headless",
        )

        battle_id = decoded_token.get("bid")

        if battle_id is None:
            raise ValueError("Battle ID not found in token.")

        return battle_id
    except jwt.InvalidTokenError:
        raise ValueError("Invalid token.")


def process_block(block_index: int, pass_type: PassType, planet_id: PlanetID):
    tx_data, tx_result_list = fetch_block_data(block_index, pass_type)

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

            if "battle" == type_id:
                if action_json.arp != "PLANETARIUM":
                    continue

                try:
                    battle_id = validate_battle_token(action_json.m)
                except ValueError:
                    continue

                sess = scoped_session(sessionmaker(bind=engine))
                try:

                    battle_history = BattleHistory(
                        planet_id=planet_id,
                        battle_id=battle_id
                    )
                    sess.add(battle_history)
                    sess.commit()
                except IntegrityError:
                    sess.rollback()
                    continue
                finally:
                    sess.remove()

            action_data[action_json.type_id].append({
                "tx_id": tx["id"],
                "agent_addr": tx["signer"].lower(),
                "avatar_addr": action_json.avatar_addr.lower(),
                "count_base": action_json.count_base,
            })

    send_sqs_message(REGION, CURRENT_PLANET, SQS_URL, block_index, action_data)


def main():
    sess = scoped_session(sessionmaker(bind=engine))
    # Get missing blocks
    start_block = int(os.environ.get("START_BLOCK_INDEX"))
    expected_all = set(range(int(os.environ.get("START_BLOCK_INDEX")), get_block_tip() + 1))
    all_blocks = set(sess.scalars(select(Block.index).where(
        Block.planet_id == CURRENT_PLANET,
        Block.pass_type == PassType.COURAGE_PASS,
        Block.index >= start_block,
    )).fetchall())
    missing_blocks = expected_all - all_blocks

    block_dict = {}
    with concurrent.futures.ThreadPoolExecutor() as executor:
        for index in missing_blocks:
            block_dict[executor.submit(process_block, index, PassType.COURAGE_PASS, CURRENT_PLANET)] = (index, PassType.COURAGE_PASS)

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
