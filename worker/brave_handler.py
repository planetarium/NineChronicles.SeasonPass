import logging
import os
import random
from typing import List, Dict

import requests
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker, scoped_session

from common.enums import ActionType
from common.models.action import Block
from common.models.season_pass import SeasonPass, Level
from common.models.user import UserSeasonPass
from common.utils.season_pass import get_current_season
from consts import HOST_LIST
from schemas.sqs import SQSMessage
from utils.stake import StakeAPCoef

AP_PER_ADVENTURE = 5
STAGE = os.environ.get("STAGE", "development")
GQL_URL = f"{random.choice(HOST_LIST[STAGE])}/graphql"

engine = create_engine(os.environ.get("DB_URI"))
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
                    season_pass_id=current_season.id,
                    agent_addr=d["agent_addr"],
                    avatar_addr=d["avatar_addr"],
                    level=0, exp=0,
                )
                season_pass_dict[d["avatar_addr"]] = new_season
                sess.add(new_season)

    return season_pass_dict


def apply_exp(user_season_dict: Dict[str, UserSeasonPass], exp: int, level_dict: Dict[int, int],
              action_data: List[Dict]):
    for d in action_data:
        target = user_season_dict[d["avatar_addr"]]
        target.exp += exp * d["count_base"]
        if target.level < max(level_dict.keys()) and target.exp >= level_dict[target.level + 1]:
            target.level += 1


def handle_sweep(user_season_dict: Dict[str, UserSeasonPass], exp: int, level_dict: Dict[int, int],
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
        if target.level < max(level_dict.keys()) and target.exp >= level_dict[target.level + 1]:
            target.level += 1


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
                    apply_exp(user_season_dict, current_season.exp_dict[ActionType.RAID], level_dict, action_data)
                    logging.info(f"{len(action_data)} Raid applied.")
                elif "battle_arena" in type_id:
                    apply_exp(user_season_dict, current_season.exp_dict[ActionType.ARENA], level_dict, action_data)
                    logging.info(f"{len(action_data)} Arena applied.")
                elif "sweep" in type_id:
                    handle_sweep(user_season_dict, current_season.exp_dict[ActionType.SWEEP], level_dict, action_data,
                                 body["stake"])
                    logging.info(f"{len(action_data)} Sweep applied.")
                else:
                    apply_exp(user_season_dict, current_season.exp_dict[ActionType.HAS], level_dict, action_data)
                    logging.info(f"{len(action_data)} HackAndSlash applied.")

            sess.add_all(list(user_season_dict.values()))
            sess.add(Block(index=body['block']))
            sess.commit()
            logging.info(f"All brave exp for block {body['block']} applied.")

    finally:
        if sess is not None:
            sess.rollback()
            sess.close()
