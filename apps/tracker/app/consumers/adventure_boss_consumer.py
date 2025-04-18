from collections import defaultdict
from datetime import datetime

import structlog
from shared.enums import ActionType, PassType, PlanetID
from shared.models.action import AdventureBossHistory, Block
from shared.models.season_pass import Level
from shared.schemas.message import TrackerMessage
from shared.utils.season_pass import get_pass
from sqlalchemy import create_engine, select
from sqlalchemy.orm import scoped_session, sessionmaker

from app.config import config
from app.utils.exp import apply_exp
from app.utils.gql import get_explore_floor
from app.utils.season_pass import apply_exp, fetch_adv_boss_history, verify_season_pass

AP_PER_ACTION = 2
logger = structlog.get_logger(__name__)

engine = create_engine(str(config.pg_dsn))


def consume_adventure_boss_message(message: TrackerMessage):
    """
    Receive action data from adv_boss_tracker and give adv.boss exp. to avatar.

    {
        "planet_id": str,
        "block": int,
        "pass_type": PassType.ADVENTURE_BOSS_PASS,
        "action_data": {
            "wanted##": [
                {
                    "season_index": int,
                    "agent_addr": str,
                    "avatar_addr": str,
                    "count_base": int
                }
            ],
            "explore_adventure_boss##": [
                ...
            ],
            "sweep_adventure_boss##": [
                ...
            ],
        }
    }
    """

    sess = scoped_session(sessionmaker(bind=engine))

    try:
        current_pass = get_pass(
            sess,
            pass_type=PassType.ADVENTURE_BOSS_PASS,
            validate_current=True,
            include_exp=True,
        )

        # Skip blocks before season starts
        if current_pass is None:
            logger.warning(
                "There is no active pass",
                pass_type=PassType.ADVENTURE_BOSS_PASS,
                timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            )

            block_index = message.block
            planet_id = PlanetID(bytes(message.planet_id, "utf-8"))
            if sess.scalar(
                select(Block).where(
                    Block.planet_id == planet_id,
                    Block.pass_type == PassType.ADVENTURE_BOSS_PASS,
                    Block.index == block_index,
                )
            ):
                logger.warning(
                    f"Planet {planet_id.name} : Block {block_index} already applied. Skip."
                )

            sess.add(
                Block(
                    planet_id=planet_id,
                    index=block_index,
                    pass_type=PassType.ADVENTURE_BOSS_PASS,
                )
            )
            logger.info(
                f"Skip adv.boss exp for {planet_id.name} : #{block_index} before season starts."
            )
            sess.commit()
            return

        level_dict = {
            x.level: x.exp
            for x in sess.scalars(
                select(Level).where(Level.pass_type == PassType.ADVENTURE_BOSS_PASS)
            ).fetchall()
        }

        block_index = message.block
        planet_id = PlanetID(bytes(message.planet_id, "utf-8"))
        if sess.scalar(
            select(Block).where(
                Block.planet_id == planet_id,
                Block.index == block_index,
                Block.pass_type == PassType.ADVENTURE_BOSS_PASS,
            )
        ):
            logger.warning(
                f"Planet {planet_id.name} : Block {block_index} already applied. Skip."
            )
            return

        user_season_dict = verify_season_pass(
            sess, planet_id, current_pass, message.action_data
        )
        all_avatar_dict = defaultdict(set)
        for type_id, action_data in message.action_data.items():
            if type_id in ("explore_adventure_boss", "sweep_adventure_boss"):
                for action in action_data:
                    all_avatar_dict[action["season_index"]].add(action["avatar_addr"])

        explore_dict = {}
        for season_index, avatars in all_avatar_dict.items():
            explore_dict[season_index] = fetch_adv_boss_history(
                sess, planet_id, season_index, list(avatars)
            )

        for type_id, action_data in message.action_data.items():
            if type_id == "wanted":
                apply_exp(
                    sess,
                    planet_id,
                    user_season_dict,
                    ActionType.WANTED,
                    current_pass.exp_dict[ActionType.WANTED],
                    level_dict,
                    block_index,
                    action_data,
                )
            elif type_id == "sweep_adventure_boss":
                # Use existing explore data: sweep only can reach to explored floor
                for action in action_data:
                    explore_data = explore_dict.get(action["season_index"], {}).get(
                        action["avatar_addr"], None
                    )
                    if explore_data:
                        action["count_base"] = explore_data.floor
                    else:
                        # Get current floor data from chain
                        # NOTE: Do not save this to DB because this can make confusion to explore action
                        current_floor = get_explore_floor(
                            config.gql_url_map[planet_id.decode()],
                            block_index,
                            action["season_index"],
                            action["avatar_addr"],
                            config.headless_jwt_secret,
                        )
                        action["count_base"] = current_floor
                apply_exp(
                    sess,
                    planet_id,
                    user_season_dict,
                    ActionType.RUSH,
                    current_pass.exp_dict[ActionType.RUSH],
                    level_dict,
                    block_index,
                    action_data,
                )
            elif type_id == "explore_adventure_boss":
                # Get floor data before explore
                for action in action_data:
                    current_floor = get_explore_floor(
                        config.gql_url_map[planet_id.decode()],
                        block_index,
                        action["season_index"],
                        action["avatar_addr"],
                        config.headless_jwt_secret,
                    )
                    explore_data = explore_dict.get(action["season_index"], {}).get(
                        action["avatar_addr"], None
                    )
                    if explore_data:
                        action["count_base"] = AP_PER_ACTION * min(
                            abs(current_floor - explore_data.floor) + 1, 5
                        )
                        explore_data.floor = current_floor
                    else:
                        # FIXME: 이렇게 하면 상태를 엄청 많이 가져와야 하는데 지금 이거 말고는 어떻게 할 수 있는 방법이 없다...
                        current_floor = get_explore_floor(
                            config.gql_url_map[planet_id.decode()],
                            block_index,
                            action["season_index"],
                            action["avatar_addr"],
                            config.headless_jwt_secret,
                        )
                        explore_data = AdventureBossHistory(
                            planet_id=planet_id,
                            season=action["season_index"],
                            agent_addr=action["agent_addr"],
                            avatar_addr=action["avatar_addr"],
                            floor=current_floor,
                        )
                        action["count_base"] = AP_PER_ACTION * min(current_floor + 1, 5)
                    sess.add(explore_data)
                apply_exp(
                    sess,
                    planet_id,
                    user_season_dict,
                    ActionType.CHALLENGE,
                    current_pass.exp_dict[ActionType.CHALLENGE],
                    level_dict,
                    block_index,
                    action_data,
                )

        sess.add_all(list(user_season_dict.values()))
        sess.add(
            Block(
                planet_id=planet_id,
                index=block_index,
                pass_type=PassType.ADVENTURE_BOSS_PASS,
            )
        )
        sess.commit()
        logger.info(
            f"All {len(user_season_dict.values())} adv.boss exp for block {planet_id.name}:{message.block} applied."
        )
    except InterruptedError as e:
        sess.rollback()
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
        sess.close()
