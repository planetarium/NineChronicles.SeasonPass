from datetime import datetime

import structlog
from app.config import config
from app.schemas.message import TrackerMessage
from app.utils.season_pass import verify_season_pass
from sqlalchemy import create_engine, desc, select
from sqlalchemy.orm import scoped_session, sessionmaker

from shared.enums import PassType, PlanetID
from shared.models.action import Block
from shared.models.season_pass import Level
from shared.models.user import UserSeasonPass
from shared.utils._graphql import GQLClient
from shared.utils.season_pass import get_pass

logger = structlog.get_logger(__name__)
engine = create_engine(str(config.pg_dsn))


def consume_world_clear_message(body: str):
    """
    Receive action data from tracker and give world clear pass exp. to avatar

        {
        "block": int,
        "pass_type": PassType.WORLD_CLEAR_PASS,
        "action_data": {
            "hack_and_slash##": [
                {
                    "agent_addr": str,
                    "avatar_addr": str,
                    "count_base": int
                }
            ],
        }
    }
    """
    message = TrackerMessage.model_validate(body)
    sess = scoped_session(sessionmaker(bind=engine))

    try:
        current_pass = get_pass(
            sess,
            pass_type=PassType.WORLD_CLEAR_PASS,
            validate_current=True,
            include_exp=True,
        )

        # Skip blocks before season starts
        if current_pass is None:
            logger.warning(
                f"There is no active {PassType.WORLD_CLEAR_PASS.name} at {datetime.now().strftime('%Y-%m-%d %H:%H:%S')}"
            )

            block_index = message.block
            planet_id = PlanetID(bytes(message.planet_id, "utf-8"))
            if sess.scalar(
                select(Block).where(
                    Block.planet_id == planet_id,
                    Block.pass_type == PassType.WORLD_CLEAR_PASS,
                    Block.index == block_index,
                )
            ):
                logger.warning(
                    f"Planet {planet_id.name} : Block {block_index} already applied. Skip."
                )
                return

            sess.add(
                Block(
                    planet_id=planet_id,
                    index=block_index,
                    pass_type=PassType.WORLD_CLEAR_PASS,
                )
            )
            logger.info(
                f"Skip world clear exp for {planet_id.name} : #{block_index} before season starts."
            )
            sess.commit()
            return

        level_list = sess.scalars(
            select(Level)
            .where(Level.pass_type == PassType.WORLD_CLEAR_PASS)
            .order_by(desc(Level.level))
        ).fetchall()

        block_index = message.block
        planet_id = PlanetID(bytes(message.planet_id, "utf-8"))
        if sess.scalar(
            select(Block).where(
                Block.planet_id == planet_id,
                Block.pass_type == PassType.WORLD_CLEAR_PASS,
                Block.index == block_index,
            )
        ):
            logger.warning(
                f"Planet {planet_id.name} : Block {block_index} already applied. Skip."
            )
            return

        user_season_dict = verify_season_pass(
            sess, planet_id, current_pass, message.action_data
        )
        for type_id, action_data in message.action_data.items():
            if type_id == "hack_and_slash22":
                for action in action_data:
                    target_data = user_season_dict.get(action["avatar_addr"], None)
                    if target_data is None:
                        user_season_dict[action["avatar_addr"]] = target_data = (
                            UserSeasonPass(
                                planet_id=planet_id,
                                agent_addr=action["agent_addr"],
                                avatar_addr=action["avatar_addr"],
                                season_pass_id=current_pass.id,
                            )
                        )

                    # Use `level` field as world, `exp` field as stage
                    if (
                        action["stage_id"] <= target_data.exp
                    ):  # Already cleared stage. pass.
                        continue
                    else:  # HAS new stage
                        client = GQLClient(
                            config.gql_url_map[planet_id.decode()],
                            config.headless_jwt_secret,
                        )
                        cleared_world, target_data.exp = client.get_last_cleared_stage(
                            action["avatar_addr"]
                        )

                    for level in level_list:
                        if level.exp <= target_data.exp:
                            target_data.level = level.level
                            break

        sess.add_all(list(user_season_dict.values()))
        sess.add(
            Block(
                planet_id=planet_id,
                index=block_index,
                pass_type=PassType.WORLD_CLEAR_PASS,
            )
        )
        sess.commit()
        logger.info(
            f"All {len(user_season_dict.values())} world clear for block {planet_id.name}:{message.block} applied."
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
