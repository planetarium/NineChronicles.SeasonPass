import os
from datetime import datetime

from sqlalchemy import create_engine, select, desc
from sqlalchemy.orm import sessionmaker, scoped_session

from common import logger
from common.enums import PassType, PlanetID
from common.models.action import Block
from common.models.season_pass import Level
from common.models.user import UserSeasonPass
from common.utils.aws import fetch_secrets
from common.utils.season_pass import get_pass
from schemas.sqs import SQSMessage
from utils.gql import get_last_cleared_stage
from utils.season_pass import verify_season_pass

DB_URI = os.environ.get("DB_URI")
db_password = fetch_secrets(os.environ.get("REGION_NAME"), os.environ.get("SECRET_ARN"))["password"]
DB_URI = DB_URI.replace("[DB_PASSWORD]", db_password)

engine = create_engine(DB_URI)


def handle(event, context):
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
    sess = None
    message = SQSMessage(Records=event.get("Records", []))

    try:
        sess = scoped_session(sessionmaker(bind=engine))
        current_pass = get_pass(sess, pass_type=PassType.WORLD_CLEAR_PASS, validate_current=True, include_exp=True)

        # Skip blocks before season starts
        if current_pass is None:
            logger.warning(
                f"There is no active {PassType.ADVENTURE_BOSS_PASS.name} at {datetime.now().strftime('%Y-%m-%d %H:%H:%S')}"
            )
            for i, record in enumerate(message.Records):
                body = record.body
                block_index = body["block"]
                planet_id = PlanetID(bytes(body["planet_id"], "utf-8"))
                sess.add(Block(planet_id=planet_id, index=block_index, pass_type=PassType.ADVENTURE_BOSS_PASS))
                logger.info(
                    f"Skip adv.boss exp for {planet_id.name} : #{block_index} before season starts."
                )
            sess.commit()
            return

        level_list = sess.scalars(select(Level).where(Level.pass_type == PassType.WORLD_CLEAR_PASS)
                                  .order_by(desc(Level.level))
                                  ).fetchall()

        for i, record in enumerate(message.Records):
            body = record.body
            block_index = body["block"]
            planet_id = PlanetID(bytes(body["planet_id"], "utf-8"))
            if sess.scalar(select(Block).where(
                    Block.planet_id == planet_id,
                    Block.pass_type == PassType.WORLD_CLEAR_PASS,
                    Block.index == block_index,
            )):
                logger.warning(f"Planet {planet_id.name} : Block {block_index} already applied. Skip.")
                continue

            user_season_dict = verify_season_pass(sess, planet_id, current_pass, body["action_data"])
            for type_id, action_data in body["action_data"].items():
                if type_id == "hack_and_slash22":
                    for action in action_data:
                        target_data = user_season_dict.get(action["avatar_addr"], None)
                        if target_data is None:
                            user_season_dict[action["avatar_addr"]] = target_data = UserSeasonPass(
                                planet_id=planet_id,
                                agent_addr=action["agent_addr"],
                                avatar_addr=action["avatar_addr"],
                                season_pass_id=current_pass.id,
                            )

                        # Use `level` field as world, `exp` field as stage
                        if action["stage_id"] <= target_data.exp:  # Already cleared stage. pass.
                            continue
                        elif target_data.exp == 0:  # No data: Get last cleared block from chain
                            cleared_world, target_data.exp = get_last_cleared_stage(planet_id, action["avatar_addr"])
                        else:  # HAS new stage
                            target_data.exp = action["stage_id"]

                        for level in level_list:
                            if level.exp <= target_data.exp:
                                target_data.level = level.level
                                break
            sess.add_all(list(user_season_dict.values()))
            sess.add(Block(planet_id=planet_id, index=block_index, pass_type=PassType.WORLD_CLEAR_PASS))
            sess.commit()
            logger.info(
                f"All {len(user_season_dict.values())} world clear for block {planet_id.name}:{body['block']} applied."
            )
    except InterruptedError as e:
        err_msg = str(e).split("\n")[0]
        detail = str(e).split("\n")[1]
        if err_msg == '(psycopg2.errors.UniqueViolation) duplicate key value violates unique constraint "block_by_pass_planet_unique"':
            logger.warning(f"{err_msg} :: {detail}")
        else:
            raise e
    finally:
        if sess is not None:
            sess.rollback()
            sess.close()
