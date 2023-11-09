import logging
import os
import random
from typing import List, Dict

import requests
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker, scoped_session

from common.enums import ActionType, PlanetID
from common.models.action import Block, ActionHistory
from common.models.season_pass import SeasonPass, Level
from common.models.user import UserSeasonPass
from common.utils.aws import fetch_secrets
from common.utils.season_pass import get_current_season
from consts import HOST_LIST
from schemas.sqs import SQSMessage
from utils.stake import StakeAPCoef

AP_PER_ADVENTURE = 5
STAGE = os.environ.get("STAGE", "development")
GQL_URL = f"{random.choice(HOST_LIST[STAGE])}/graphql"

DB_URI = os.environ.get("DB_URI")
db_password = fetch_secrets(os.environ.get("REGION_NAME"), os.environ.get("SECRET_ARN"))["password"]
DB_URI = DB_URI.replace("[DB_PASSWORD]", db_password)
engine = create_engine(DB_URI)
ap_coef = StakeAPCoef(GQL_URL)


def verify_season_pass(sess, current_season: SeasonPass, action_data: Dict[str, List]) -> Dict[str, UserSeasonPass]:
    avatar_list = set()
    for data in action_data.values():
        for d in data:
            avatar_list.add(d["avatar_addr"])

    season_pass_dict = {x.avatar_addr: x for x in sess.scalars(
        select(UserSeasonPass)
        .where(UserSeasonPass.season_pass_id == current_season.id, UserSeasonPass.avatar_addr.in_(avatar_list)
               )
    ).fetchall()}

    for data in action_data.values():
        for d in data:
            if d["avatar_addr"] not in season_pass_dict:
                new_season = UserSeasonPass(
                    planet_id=data.get("planet_id", PlanetID.ODIN if STAGE == "mainnet" else PlanetID.ODIN_INTERNAL),
                    season_pass_id=current_season.id,
                    agent_addr=d["agent_addr"],
                    avatar_addr=d["avatar_addr"],
                    level=0, exp=0,
                )
                season_pass_dict[d["avatar_addr"]] = new_season
                sess.add(new_season)

    return season_pass_dict


def apply_exp(sess, user_season_dict: Dict[str, UserSeasonPass], action_type: ActionType, exp: int,
              level_dict: Dict[int, int],
              action_data: List[Dict]):
    for d in action_data:
        target = user_season_dict[d["avatar_addr"]]
        target.exp += exp * d["count_base"]

        for lvl in sorted(level_dict.keys(), reverse=True):
            if target.exp >= level_dict[lvl]:
                target.level = lvl
                break

        sess.add(ActionHistory(
            season_id=target.season_pass_id,
            agent_addr=target.agent_addr, avatar_addr=target.avatar_addr,
            action=action_type, count=d["count_base"], exp=exp * ["count_base"],
        ))


def handle_sweep(sess, user_season_dict: Dict[str, UserSeasonPass], exp: int, level_dict: Dict[int, int],
                 action_data: List[Dict], coef_dict: Dict[str, int]):
    for d in action_data:
        coef = coef_dict.get(d["agent_addr"])
        if not coef:
            resp = requests.post(GQL_URL, json={
                "query": f"""{{ stateQuery {{ stakeState(address: "{d['agent_addr']}") {{ deposit }} }} }}"""})
            data = resp.json()["data"]["stateQuery"]["stakeState"]
            if data is None:
                coef = 100
            else:
                coef = ap_coef.get_ap_coef(float(data["deposit"]))

        real_count = d["count_base"] // (AP_PER_ADVENTURE * coef / 100)
        target = user_season_dict[d["avatar_addr"]]
        target.exp += exp * real_count

        for lvl in sorted(level_dict.keys(), reverse=True):
            if target.exp >= level_dict[lvl]:
                target.level = lvl
                break

        sess.add(ActionHistory(
            season_id=target.season_pass_id,
            agent_addr=target.agent_addr, avatar_addr=target.avatar_addr,
            action=ActionType.SWEEP, count=real_count, exp=exp * real_count,
        ))


def handle(event, context):
    """
    Receive action data from block_tracker and give brave exp. to avatar.

    {
        "block": int,
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
        },
        "stake": {
            str: int  # Address : Ap Coefficient by Staking pair.
        }
    }
    """
    sess = None
    message = SQSMessage(Records=event.get("Records", []))

    try:
        sess = scoped_session(sessionmaker(bind=engine))
        current_season = get_current_season(sess, include_exp=True)
        level_dict = {x.level: x.exp for x in sess.scalars(select(Level)).fetchall()}

        for i, record in enumerate(message.Records):
            if sess.scalar(select(Block).where(Block.index == record.body["block"])):
                logging.warning(f"Block index {record.body['block']} already applied. Skip.")
                continue

            body = record.body
            user_season_dict = verify_season_pass(sess, current_season, body["action_data"])
            for type_id, action_data in body["action_data"].items():
                if "raid" in type_id:
                    apply_exp(sess, user_season_dict, ActionType.RAID, current_season.exp_dict[ActionType.RAID],
                              level_dict, action_data)
                    logging.info(f"{len(action_data)} Raid applied.")
                elif "battle_arena" in type_id:
                    apply_exp(sess, user_season_dict, ActionType.ARENA, current_season.exp_dict[ActionType.ARENA],
                              level_dict, action_data)
                    logging.info(f"{len(action_data)} Arena applied.")
                elif "sweep" in type_id:
                    handle_sweep(sess, user_season_dict, current_season.exp_dict[ActionType.SWEEP], level_dict,
                                 action_data,
                                 body["stake"])
                    logging.info(f"{len(action_data)} Sweep applied.")
                else:
                    apply_exp(sess, user_season_dict, ActionType.HAS, current_season.exp_dict[ActionType.HAS],
                              level_dict, action_data)
                    logging.info(f"{len(action_data)} HackAndSlash applied.")

            sess.add_all(list(user_season_dict.values()))
            sess.add(Block(index=body['block']))
            sess.commit()
            logging.info(f"All brave exp for block {body['block']} applied.")

    finally:
        if sess is not None:
            sess.rollback()
            sess.close()
