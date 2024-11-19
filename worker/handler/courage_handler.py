import os
from typing import List, Dict

import requests
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker, scoped_session

from common import logger
from common.enums import ActionType, PlanetID, PassType
from common.models.action import Block, ActionHistory
from common.models.season_pass import Level
from common.models.user import UserSeasonPass
from common.utils.aws import fetch_secrets
from common.utils.season_pass import get_pass, create_jwt_token
from schemas.sqs import SQSMessage
from utils.season_pass import verify_season_pass, apply_exp
from utils.stake import StakeAPCoef

AP_PER_ADVENTURE = 5
STAGE = os.environ.get("STAGE", "development")

DB_URI = os.environ.get("DB_URI")
db_password = fetch_secrets(os.environ.get("REGION_NAME"), os.environ.get("SECRET_ARN"))["password"]
DB_URI = DB_URI.replace("[DB_PASSWORD]", db_password)

if STAGE == "mainnet":
    GQL_DICT = {
        PlanetID.ODIN: os.environ.get("ODIN_GQL_URL"),
        PlanetID.HEIMDALL: os.environ.get("HEIMDALL_GQL_URL"),
        PlanetID.THOR: os.environ.get("THOR_GQL_URL"),
    }
else:
    GQL_DICT = {
        PlanetID.ODIN_INTERNAL: os.environ.get("ODIN_GQL_URL"),
        PlanetID.HEIMDALL_INTERNAL: os.environ.get("HEIMDALL_GQL_URL"),
        PlanetID.THOR_INTERNAL: os.environ.get("THOR_GQL_URL"),
    }

engine = create_engine(DB_URI)
ap_coef = StakeAPCoef(jwt_secret=os.environ.get("HEADLESS_GQL_JWT_SECRET"))
coef_dict = {}


def handle_sweep(sess, planet_id: PlanetID, user_season_dict: Dict[str, UserSeasonPass], exp: int,
                 level_dict: Dict[int, int], block_index: int, action_data: List[Dict]):
    GQL_URL = GQL_DICT[planet_id]
    ap_coef.set_url(gql_url=GQL_URL)
    for d in action_data:
        coef = coef_dict.get(d["agent_addr"])
        if not coef:
            resp = requests.post(
                GQL_URL,
                json={
                    "query": f"""{{ stateQuery {{ stakeState(address: "{d['agent_addr']}") {{ deposit }} }} }}"""},
                headers={
                    "Authorization": f"Bearer {create_jwt_token(os.environ.get('HEADLESS_GQL_JWT_SECRET'))}"}
            )
            data = resp.json()["data"]["stateQuery"]["stakeState"]
            if data is None:
                coef = 100
            else:
                coef = ap_coef.get_ap_coef(float(data["deposit"]))
            coef_dict[d["agent_addr"]] = coef

        real_count = d["count_base"] // (AP_PER_ADVENTURE * coef / 100)

        if exp * real_count < 0:
            logger.warning(f"[Report] Account {d['agent_addr']} may abuse sweep with count: {real_count}")
            continue

        target = user_season_dict[d["avatar_addr"]]
        target.exp += exp * real_count

        for lvl in sorted(level_dict.keys(), reverse=True):
            if target.exp >= level_dict[lvl]:
                target.level = lvl
                break

        sess.add(ActionHistory(
            planet_id=planet_id,
            block_index=block_index, tx_id=d.get("tx_id", "0" * 64),
            season_id=target.season_pass_id,
            agent_addr=target.agent_addr, avatar_addr=target.avatar_addr,
            action=ActionType.SWEEP, count=real_count, exp=exp * real_count,
        ))


def handle(event, context):
    """
    Receive action data from block_tracker and give brave exp. to avatar.

    {
        "planet_id": str,
        "block": int,
        "pass_type": PassType,
        "action_data": {
            "hack_and_slash##": [
                {
                    "agent_addr": str,
                    "avatar_addr": str,
                    "count_base": int  # Be aware this could be real count or used AP point(for Sweep)
                },
                ...
            ],
            "hack_and_slash_sweep##: [
                ...
            ],
            "battle_arena##: [
                ...
            ],
            "raid##: [
                ...
            ],
        }
    }
    """
    sess = None
    message = SQSMessage(Records=event.get("Records", []))

    try:
        sess = scoped_session(sessionmaker(bind=engine))
        current_pass = get_pass(sess, pass_type=PassType.COURAGE_PASS, validate_current=True, include_exp=True)
        level_dict = {x.level: x.exp for x in
                      sess.scalars(select(Level).where(Level.pass_type == PassType.COURAGE_PASS)).fetchall()}

        for i, record in enumerate(message.Records):
            body = record.body
            block_index = body["block"]
            planet_id = PlanetID(bytes(body["planet_id"], "utf-8"))

            if sess.scalar(select(Block).where(
                    Block.planet_id == planet_id,
                    Block.index == block_index,
                    Block.pass_type == PassType.COURAGE_PASS,
            )):
                logger.warning(f"Planet {planet_id.name} : Block {block_index} already applied. Skip.")
                continue

            user_season_dict = verify_season_pass(sess, planet_id, current_pass, body["action_data"])
            for type_id, action_data in body["action_data"].items():
                if "random_buff" in type_id or "raid_reward" in type_id:
                    continue

                if "raid" in type_id:
                    apply_exp(sess, planet_id, user_season_dict, ActionType.RAID,
                              current_pass.exp_dict[ActionType.RAID], level_dict, block_index, action_data)
                    logger.info(f"{len(action_data)} Raid applied.")
                elif "battle_arena" in type_id:
                    apply_exp(sess, planet_id, user_season_dict, ActionType.ARENA,
                              current_pass.exp_dict[ActionType.ARENA], level_dict, block_index, action_data)
                    logger.info(f"{len(action_data)} Arena applied.")
                elif "sweep" in type_id:
                    handle_sweep(sess, planet_id, user_season_dict, current_pass.exp_dict[ActionType.SWEEP],
                                 level_dict, block_index, action_data)
                    logger.info(f"{len(action_data)} Sweep applied.")
                elif "event_dungeon" in type_id:
                    apply_exp(sess, planet_id, user_season_dict, ActionType.EVENT,
                              current_pass.exp_dict[ActionType.EVENT], level_dict, block_index, action_data)
                    logger.info(f"{len(action_data)} Event Dungeon applied.")
                else:
                    apply_exp(sess, planet_id, user_season_dict, ActionType.HAS,
                              current_pass.exp_dict[ActionType.HAS], level_dict, block_index, action_data)
                    logger.info(f"{len(action_data)} HackAndSlash applied.")

            sess.add_all(list(user_season_dict.values()))
            sess.add(Block(planet_id=planet_id, index=block_index, pass_type=PassType.COURAGE_PASS))
            sess.commit()
            logger.info(f"All {len(user_season_dict.values())} brave exp for block {body['block']} applied.")
    except IntegrityError as e:
        err_msg = str(e).split("\n")[0]
        detail = str(e).split("\n")[1]
        if err_msg == '(psycopg2.errors.UniqueViolation) duplicate key value violates unique constraint "block_by_planet_pass_type_unique"':
            logger.warning(f"{err_msg} :: {detail}")
        else:
            raise e
    finally:
        if sess is not None:
            sess.rollback()
            sess.close()
