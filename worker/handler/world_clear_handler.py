import os

from sqlalchemy import create_engine, select, desc
from sqlalchemy.orm import sessionmaker, scoped_session

from common import logger
from common.enums import PassType, PlanetID
from common.models.action import Block
from common.models.season_pass import Level
from common.models.user import UserSeasonPass
from common.utils.aws import fetch_secrets
from common.utils.season_pass import get_pass
from courage_handler import verify_season_pass
from utils.gql import get_last_cleared_stage
from worker.schemas.sqs import SQSMessage

DB_URI = os.environ.get("DB_URI")
db_password = fetch_secrets(os.environ.get("REGION_NAME"), os.environ.get("SECRET_ARN"))["password"]
DB_URI = DB_URI.replace("[DB_PASSWORD]", db_password)

# See WorldSheet.csv in Lib9c to add new world
WORLD_STAGE_DICT = {
    1: {"min": 1, "max": 50},
    2: {"min": 51, "max": 100},
    3: {"min": 101, "max": 150},
    4: {"min": 151, "max": 200},
    5: {"min": 201, "max": 250},
    6: {"min": 251, "max": 300},
    7: {"min": 301, "max": 350},
    8: {"min": 351, "max": 400},
    # 9: {"min": 401, "max": 450},
}

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
        level_list = sess.scalars(select(Level).where(Level.pass_type == PassType.WORLD_CLEAR_PASS)
                                  .order_by(desc(Level.level))
                                  ).fetchall()

        for i, record in enumerate(message.Records):
            body = record.body
            block_index = body["block"]
            planet_id = PlanetID(bytes(body["planet_id"], "utf-8"))
            if sess.scalar(select(Block).where(
                    Block.planet_id == planet_id,
                    Block.index == block_index,
            )):
                logger.warning(f"Planet {planet_id.name} : Block {block_index} already applied. Skip.")
                continue

            user_season_dict = verify_season_pass(sess, planet_id, current_pass, body["action_data"])
            for type_id, action_data in body["action_data"].items():
                if type_id == "hack_and_slash22":
                    target_data = user_season_dict.get(action_data["avatar_addr"], None)
                    if target_data is None:
                        user_season_dict[action_data["avatar_addr"]] = target_data = UserSeasonPass(
                            planet_id=planet_id,
                            agent_addr=action_data["agent_addr"],
                            avatar_addr=action_data["avatar_addr"],
                            season_pass_id=current_pass.id,
                        )

                    # Use `level` field as world, `exp` field as stage
                    if action_data["stage_id"] <= target_data.exp:  # Already cleared stage. pass.
                        continue
                    elif target_data.exp == 0:  # No data
                        target_data.exp = get_last_cleared_stage(planet_id, action_data["avatar_addr"])
                    else:  # HAS new stage
                        target_data.exp = action_data["stage_id"]

                    for level in level_list:
                        if level.exp <= target_data.exp:
                            target_data.level = level.level
                            break
            sess.add_all(list(user_season_dict.values()))
            sess.add(Block(planet_id=planet_id, index=block_index, pass_type=PassType.WORLD_CLEAR_PASS))
            sess.commit()
            logger.info(
                f"All {len(user_season_dict.values())} adv.boss exp for block {planet_id.name}:{body['block']} applied."
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
