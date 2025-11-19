import os
from typing import Dict, List

import requests
import structlog
from shared.enums import ActionType, PassType, PlanetID
from shared.models.action import ActionHistory, Block
from shared.models.season_pass import Level
from shared.models.user import UserSeasonPass
from shared.schemas.message import TrackerMessage
from shared.utils.season_pass import create_jwt_token, get_pass
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import scoped_session, sessionmaker

from app.config import config
from app.utils.season_pass import apply_exp, verify_season_pass
from app.utils.stake import StakeAPCoef

AP_PER_ADVENTURE = 5

logger = structlog.get_logger(__name__)

engine = create_engine(str(config.pg_dsn))
ap_coef = StakeAPCoef(jwt_secret=config.headless_jwt_secret)
coef_dict = {}


def handle_sweep(
    sess,
    planet_id: PlanetID,
    user_season_dict: Dict[str, UserSeasonPass],
    exp: int,
    level_dict: Dict[int, int],
    block_index: int,
    action_data: List[Dict],
):
    gql_url = config.gql_url_map[planet_id.decode()]
    ap_coef.set_url(gql_url=gql_url)
    for d in action_data:
        coef = coef_dict.get(d["agent_addr"])
        if not coef:
            resp = requests.post(
                gql_url,
                json={
                    "query": f"""{{ stateQuery {{ stakeState(address: "{d['agent_addr']}") {{ deposit }} }} }}"""
                },
                headers={
                    "Authorization": f"Bearer {create_jwt_token(config.headless_jwt_secret)}"
                },
            )
            data = resp.json()["data"]["stateQuery"]["stakeState"]
            if data is None:
                coef = 100
            else:
                coef = ap_coef.get_ap_coef(float(data["deposit"]))
            coef_dict[d["agent_addr"]] = coef

        real_count = d["count_base"] // (AP_PER_ADVENTURE * coef / 100)

        if exp * real_count < 0:
            logger.warning(
                f"[Report] Account {d['agent_addr']} may abuse sweep with count: {real_count}"
            )
            continue

        target = user_season_dict[d["avatar_addr"]]
        target.exp += exp * real_count

        for lvl in sorted(level_dict.keys(), reverse=True):
            if target.exp >= level_dict[lvl]:
                target.level = lvl
                break

        sess.add(
            ActionHistory(
                planet_id=planet_id,
                block_index=block_index,
                tx_id=d.get("tx_id", "0" * 64),
                season_id=target.season_pass_id,
                agent_addr=target.agent_addr,
                avatar_addr=target.avatar_addr,
                action=ActionType.SWEEP,
                count=real_count,
                exp=exp * real_count,
            )
        )


def consume_courage_message(message: TrackerMessage):
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
            "battle##: [
                ...
            ],
            "raid##: [
                ...
            ],
        }
    }
    """
    sess = scoped_session(sessionmaker(bind=engine))

    try:
        current_pass = get_pass(
            sess,
            pass_type=PassType.COURAGE_PASS,
            validate_current=True,
            include_exp=True,
        )
        level_dict = {
            x.level: x.exp
            for x in sess.scalars(
                select(Level).where(Level.pass_type == PassType.COURAGE_PASS)
            ).fetchall()
        }

        block_index = message.block
        planet_id = PlanetID(bytes(message.planet_id, "utf-8"))
        
        existing_block = sess.scalar(
            select(Block).where(
                Block.planet_id == planet_id,
                Block.pass_type == PassType.COURAGE_PASS,
            )
        )

        if existing_block.last_processed_index >= block_index:
            logger.warning(
                f"Planet {planet_id.name} : Block {block_index} already applied. Skip."
            )
            return

        user_season_dict = verify_season_pass(
            sess, planet_id, current_pass, message.action_data
        )
        for type_id, action_data in message.action_data.items():
            if "random_buff" in type_id or "raid_reward" in type_id:
                continue

            if "raid7" == type_id:
                apply_exp(
                    sess,
                    planet_id,
                    user_season_dict,
                    ActionType.RAID,
                    current_pass.exp_dict[ActionType.RAID],
                    level_dict,
                    block_index,
                    action_data,
                )
                logger.info(f"{len(action_data)} Raid applied.")
            elif "battle" == type_id:
                apply_exp(
                    sess,
                    planet_id,
                    user_season_dict,
                    ActionType.ARENA,
                    current_pass.exp_dict[ActionType.ARENA],
                    level_dict,
                    block_index,
                    action_data,
                )
                logger.info(f"{len(action_data)} Arena applied.")
            elif "hack_and_slash_sweep10" == type_id:
                handle_sweep(
                    sess,
                    planet_id,
                    user_season_dict,
                    current_pass.exp_dict[ActionType.SWEEP],
                    level_dict,
                    block_index,
                    action_data,
                )
                logger.info(f"{len(action_data)} Sweep applied.")
            elif "event_dungeon_battle6" == type_id:
                apply_exp(
                    sess,
                    planet_id,
                    user_season_dict,
                    ActionType.EVENT,
                    current_pass.exp_dict[ActionType.EVENT],
                    level_dict,
                    block_index,
                    action_data,
                )
                logger.info(f"{len(action_data)} Event Dungeon applied.")
            elif "infinite_tower_battle" == type_id:
                apply_exp(
                    sess,
                    planet_id,
                    user_season_dict,
                    ActionType.INFINITE_TOWER,
                    current_pass.exp_dict[ActionType.INFINITE_TOWER],
                    level_dict,
                    block_index,
                    action_data,
                )
                logger.info(f"{len(action_data)} Infinite Tower applied.")
            else:
                apply_exp(
                    sess,
                    planet_id,
                    user_season_dict,
                    ActionType.HAS,
                    current_pass.exp_dict[ActionType.HAS],
                    level_dict,
                    block_index,
                    action_data,
                )
                logger.info(f"{len(action_data)} HackAndSlash applied.")

        sess.add_all(list(user_season_dict.values()))
        
        existing_block.last_processed_index = block_index
        
        sess.commit()
        logger.info(
            f"All {len(user_season_dict.values())} brave exp for block {message.block} applied."
        )
    except IntegrityError as e:
        sess.rollback()

        err_msg = str(e).split("\n")[0]
        detail = str(e).split("\n")[1]
        if (
            err_msg
            == '(psycopg2.errors.UniqueViolation) duplicate key value violates unique constraint "block_by_planet_pass_type_unique"'
        ):
            logger.warning(f"{err_msg} :: {detail}")
        else:
            raise e
    finally:
        sess.close()
