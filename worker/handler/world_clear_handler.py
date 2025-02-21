import json
import os
import sys
from datetime import datetime

from sqlalchemy import create_engine, desc, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import scoped_session, sessionmaker

from common import logger
from common.enums import PassType, PlanetID
from common.models.action import Block
from common.models.season_pass import Level
from common.models.user import UserSeasonPass
from common.utils.aws import fetch_secrets
from common.utils.season_pass import get_pass
from common.utils._graphql import get_last_cleared_stage
from common.utils.season_pass import get_pass
from worker.handler.courage_handler import DUPLICATED_MSG
from worker.tracker.world_clear_tracker import WORLD_QUEUE_NAME
from worker.utils.mq import get_connection
from worker.utils.season_pass import verify_season_pass

DB_URI = os.environ.get("DB_URI")
db_password = fetch_secrets(
    os.environ.get("REGION_NAME"), os.environ.get("SECRET_ARN")
)["password"]
DB_URI = DB_URI.replace("[DB_PASSWORD]", db_password)

engine = create_engine(DB_URI)


def handle(ch, method, properties, message_body):
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
    records = event.get("Records", [])

    try:
        sess = scoped_session(sessionmaker(bind=engine))
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
            for i, record in enumerate(records):
                body = record["body"]
                block_index = body["block"]
                planet_id = PlanetID(bytes(body["planet_id"], "utf-8"))
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
                    continue

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

        for i, record in enumerate(records):
            body = record["body"]
            block_index = body["block"]
            planet_id = PlanetID(bytes(body["planet_id"], "utf-8"))
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
                continue

            user_season_dict = verify_season_pass(
                sess, planet_id, current_pass, body["action_data"]
            )
            for type_id, action_data in body["action_data"].items():
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
                            cleared_world, target_data.exp = get_last_cleared_stage(
                                planet_id, action["avatar_addr"]
                            )

        block_index = body["block"]
        planet_id = PlanetID(bytes(body["planet_id"], "utf-8"))
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
            sess, planet_id, current_pass, body["action_data"]
        )
        for type_id, action_data in body["action_data"].items():
            if type_id == "hack_and_slash22":
                for action in action_data:
                    target_data = user_season_dict.get(action["avatar_addr"], None)
                    if target_data is None:
                        user_season_dict[
                            action["avatar_addr"]
                        ] = target_data = UserSeasonPass(
                            planet_id=planet_id,
                            agent_addr=action["agent_addr"],
                            avatar_addr=action["avatar_addr"],
                            season_pass_id=current_pass.id,
                        )

                    # Use `level` field as world, `exp` field as stage
                    if (
                        action["stage_id"] <= target_data.exp
                    ):  # Already cleared stage. pass.
                        continue
                    else:  # HAS new stage
                        cleared_world, target_data.exp = get_last_cleared_stage(
                            planet_id, action["avatar_addr"]
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
                f"All {len(user_season_dict.values())} world clear for block {planet_id.name}:{body['block']} applied."
            )
    except IntegrityError as e:
        err_msg = str(e).split("\n")[0]
        detail = str(e).split("\n")[1]
        if err_msg == DUPLICATED_MSG:
            logger.warning(f"{err_msg} :: {detail}")
        else:
            raise e
    finally:
        if sess is not None:
            sess.rollback()
            sess.close()


def main():
    connection = get_connection()
    channel = connection.channel()

    channel.queue_declare(queue=WORLD_QUEUE_NAME)
    channel.basic_consume(
        queue=WORLD_QUEUE_NAME, on_message_callback=handle, auto_ack=True
    )

    print(f" [*] Waiting for {WORLD_QUEUE_NAME} messages. To exit press CTRL+C")
    channel.start_consuming()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Interrupted")
        try:
            sys.exit(0)
        except SystemExit:
            os._exit(0)
