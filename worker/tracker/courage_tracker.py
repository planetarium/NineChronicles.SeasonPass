import base64
import concurrent.futures
import json
import os
import time
from collections import defaultdict
from typing import Dict, List

import jwt
import requests
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import scoped_session, sessionmaker

from common import logger
from common.enums import ActionType, PassType, PlanetID
from common.models.action import ActionHistory, Block
from common.models.arena import BattleHistory
from common.models.season_pass import Level
from common.models.user import UserSeasonPass
from common.utils.season_pass import create_jwt_token, get_pass
from worker.schemas.action import ActionJson
from worker.utils.exp import apply_exp
from worker.utils.gql import fetch_block_data, get_block_tip
from worker.utils.season_pass import verify_season_pass
from worker.utils.stake import StakeAPCoef

# Constants
AP_PER_ADVENTURE = 5
STAGE = os.getenv("STAGE", "development")
CURRENT_PLANET = PlanetID(os.getenv("PLANET_ID").encode())
DB_URI = os.getenv("DB_URI")
ARENA_SERVICE_JWT_PUBLIC_KEY = os.getenv("ARENA_SERVICE_JWT_PUBLIC_KEY")

# Decode public key once
public_key_pem = base64.b64decode(ARENA_SERVICE_JWT_PUBLIC_KEY).decode("utf-8")

# Initialize database engine and coefficient calculator
engine = create_engine(DB_URI)
ap_coef = StakeAPCoef(jwt_secret=os.getenv("HEADLESS_GQL_JWT_SECRET"))
coef_dict = {}


def validate_battle_token(token: str):
    try:
        decoded_token = jwt.decode(
            token,
            public_key_pem,
            algorithms=["RS256"],
            issuer="planetarium arena service",
            audience="NineChronicles headless",
        )
        return decoded_token.get("bid") or ValueError("Battle ID not found in token.")
    except jwt.InvalidTokenError:
        raise ValueError("Invalid token.")


def handle_sweep(
    sess,
    planet_id: PlanetID,
    user_season_dict: Dict[str, UserSeasonPass],
    exp: int,
    level_dict: Dict[int, int],
    block_index: int,
    action_data: List[Dict],
):
    ap_coef.set_url(gql_url=os.getenv("GQL_URL"))
    for data in action_data:
        coef = coef_dict.get(data["agent_addr"])
        if coef is None:
            coef = fetch_and_cache_coef(data["agent_addr"])

        real_count = data["count_base"] // (AP_PER_ADVENTURE * coef / 100)
        if exp * real_count < 0:
            logger.warning(
                f"[Report] Account {data['agent_addr']} may abuse sweep with count: {real_count}"
            )
            continue

        update_user_exp_and_level(
            user_season_dict[data["avatar_addr"]], exp * real_count, level_dict
        )
        log_action_history(
            sess,
            planet_id,
            block_index,
            data,
            exp * real_count,
            real_count,
            user_season_dict,
        )


def fetch_and_cache_coef(agent_addr: str) -> int:
    response = requests.post(
        os.getenv("GQL_URL"),
        json={
            "query": f"""{{ stateQuery {{ stakeState(address: "{agent_addr}") {{ deposit }} }} }}"""
        },
        headers={
            "Authorization": f"Bearer {create_jwt_token(os.getenv('HEADLESS_GQL_JWT_SECRET'))}"
        },
    )
    data = response.json()["data"]["stateQuery"]["stakeState"]
    coef = 100 if data is None else ap_coef.get_ap_coef(float(data["deposit"]))
    coef_dict[agent_addr] = coef
    return coef


def update_user_exp_and_level(
    user: UserSeasonPass, exp_gain: int, level_dict: Dict[int, int]
):
    user.exp += exp_gain
    for level, exp_required in sorted(level_dict.items(), reverse=True):
        if user.exp >= exp_required:
            user.level = level
            break


def log_action_history(
    sess,
    planet_id: PlanetID,
    block_index: int,
    data: Dict,
    exp: int,
    count: int,
    user_season_dict: Dict[str, UserSeasonPass],
):
    sess.add(
        ActionHistory(
            planet_id=planet_id,
            block_index=block_index,
            tx_id=data.get("tx_id", "0" * 64),
            season_id=user_season_dict[data["avatar_addr"]].season_pass_id,
            agent_addr=data["agent_addr"],
            avatar_addr=data["avatar_addr"],
            action=ActionType.SWEEP,
            count=count,
            exp=exp,
        )
    )


def process_block(block_index: int, pass_type: PassType, planet_id: PlanetID, sess):
    tx_data, tx_result_list = fetch_block_data(block_index, pass_type)
    # Add a small sleep after GraphQL query
    time.sleep(0.1)
    action_data = defaultdict(list)
    agent_list = set()

    for tx, result in zip(tx_data, tx_result_list):
        if result != "SUCCESS":
            continue

        for action in tx["actions"]:
            action_raw = json.loads(action["json"].replace(r"\uFEFF", ""))
            type_id = action_raw["type_id"]

            if any(
                keyword in type_id
                for keyword in ["random_buff", "claim", "raid_reward"]
            ):
                continue

            agent_list.add(tx["signer"].lower())
            action_json = ActionJson(type_id=type_id, **action_raw["values"])

            if type_id == "battle" and action_json.arp == "PLANETARIUM":
                try:
                    battle_id = validate_battle_token(action_json.m)
                    sess.add(BattleHistory(planet_id=planet_id, battle_id=battle_id))
                except ValueError:
                    continue

            action_data[action_json.type_id].append(
                {
                    "tx_id": tx["id"],
                    "agent_addr": tx["signer"].lower(),
                    "avatar_addr": action_json.avatar_addr.lower(),
                    "count_base": action_json.count_base,
                }
            )

    process_actions(sess, planet_id, block_index, action_data)


def process_actions(
    sess, planet_id: PlanetID, block_index: int, action_data: Dict[str, List[Dict]]
):
    current_pass = get_pass(
        sess, pass_type=PassType.COURAGE_PASS, validate_current=True, include_exp=True
    )
    level_dict = {
        x.level: x.exp
        for x in sess.scalars(
            select(Level).where(Level.pass_type == PassType.COURAGE_PASS)
        ).fetchall()
    }
    user_season_dict = verify_season_pass(sess, planet_id, current_pass, action_data)

    try:
        for type_id, actions in action_data.items():
            if "random_buff" in type_id or "raid_reward" in type_id:
                continue

            action_type = determine_action_type(type_id)
            apply_exp(
                sess,
                planet_id,
                user_season_dict,
                action_type,
                current_pass.exp_dict[action_type],
                level_dict,
                block_index,
                actions,
            )
            logger.info(f"{len(actions)} {action_type.name} applied.")

        sess.add_all(user_season_dict.values())
        sess.add(
            Block(
                planet_id=planet_id, index=block_index, pass_type=PassType.COURAGE_PASS
            )
        )
        sess.commit()
        logger.info(
            f"All {len(user_season_dict.values())} brave exp for block {block_index} applied."
        )
    except IntegrityError as e:
        err_msg = str(e).split("\n")[0]
        detail = str(e).split("\n")[1]
        if (
            err_msg
            == '(psycopg2.errors.UniqueViolation) duplicate key value violates unique constraint "block_by_pass_planet_unique"'
        ):
            logger.warning(f"{err_msg} :: {detail}")
        else:
            raise e
    finally:
        if sess is not None:
            sess.rollback()
            sess.close()


def determine_action_type(type_id: str) -> ActionType:
    if "raid" in type_id:
        return ActionType.RAID
    elif "battle" in type_id:
        return ActionType.ARENA
    elif "sweep" in type_id:
        return ActionType.SWEEP
    elif "event_dungeon" in type_id:
        return ActionType.EVENT
    else:
        return ActionType.HAS


def main():
    while True:
        sess = scoped_session(sessionmaker(bind=engine))
        start_block = int(os.getenv("START_BLOCK_INDEX"))
        expected_all = set(range(start_block, get_block_tip() + 1))
        all_blocks = set(
            sess.scalars(
                select(Block.index).where(
                    Block.planet_id == CURRENT_PLANET,
                    Block.pass_type == PassType.COURAGE_PASS,
                    Block.index >= start_block,
                )
            ).fetchall()
        )
        missing_blocks = expected_all - all_blocks

        block_dict = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            for index in missing_blocks:
                block_dict[
                    executor.submit(
                        process_block,
                        index,
                        PassType.COURAGE_PASS,
                        CURRENT_PLANET,
                        sess,
                    )
                ] = index

            for future in concurrent.futures.as_completed(block_dict):
                index = block_dict[future]
                exc = future.exception()

                if exc:
                    logger.error(f"Error occurred processing block {index} :: {exc}")
                else:
                    logger.info(f"Block {index} processed successfully.")
        time.sleep(8)


if __name__ == "__main__":
    main()
