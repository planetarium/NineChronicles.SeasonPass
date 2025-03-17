import json
import time
from collections import defaultdict

import structlog
from shared.enums import PassType
from shared.models.action import Block
from shared.schemas.message import TrackerMessage
from sqlalchemy import create_engine, select
from sqlalchemy.orm import scoped_session, sessionmaker

from app.celery import send_to_worker
from app.config import config
from app.schemas.action import AdventureBossActionJson
from app.utils.gql import fetch_block_data, get_block_tip

logger = structlog.get_logger(__name__)
engine = create_engine(str(config.pg_dsn), pool_size=5, max_overflow=5)


def track_adv_boss_actions(block_index: int):
    tx_data, tx_result_list = fetch_block_data(
        config.gql_url,
        block_index,
        PassType.ADVENTURE_BOSS_PASS,
        config.headless_jwt_secret,
    )
    time.sleep(0.2)

    action_data = defaultdict(list)
    for i, tx in enumerate(tx_data):
        if tx_result_list[i] != "SUCCESS":
            continue

        for action in tx["actions"]:
            action_raw = json.loads(action["json"].replace(r"\uFEFF", ""))
            type_id = action_raw["type_id"]

            action_json = AdventureBossActionJson(
                type_id=type_id, **(action_raw["values"])
            )
            action_data[action_json.type_id].append(
                {
                    "tx_id": tx["id"],
                    "season_index": action_json.season_index,
                    "agent_addr": tx["signer"].lower(),
                    "avatar_addr": action_json.avatar_addr.lower(),
                    "count_base": int(action_json.count_base),
                }
            )

    logger.info(
        f"Sending task to Celery worker: season_pass.process_adventure_boss",
        planet_id=config.planet_id.decode(),
        block=block_index,
        action_count=len(action_data),
    )
    message = TrackerMessage(
        planet_id=config.planet_id.decode(),
        block=block_index,
        action_data=action_data,
    ).model_dump()
    send_to_worker("season_pass.process_adventure_boss", message)


def track_missing_blocks():
    sess = scoped_session(sessionmaker(bind=engine))
    try:
        # Get missing blocks
        start_block = config.start_block_index
        expected_all = set(
            range(start_block, get_block_tip(config.gql_url, config.headless_jwt_secret))
        )  # Sloth needs 1 block to render actions: get tip-1
        all_blocks = set(
            sess.scalars(
                select(Block.index).where(
                    Block.planet_id == config.planet_id,
                    Block.index >= start_block,
                    Block.pass_type == PassType.ADVENTURE_BOSS_PASS,
                )
            ).fetchall()
        )
        missing_blocks = expected_all - all_blocks
    except Exception as e:
        raise e
    finally:
        sess.close()

    logger.info(
        "Missing blocks",
        tracker="adv_boss_tracker",
        missing_blocks=[missing_blocks],
        planet_id=config.planet_id.decode(),
        start_block=start_block,
    )
    for index in missing_blocks:
        try:
            result = track_adv_boss_actions(index)
            logger.info(f"Block {index} collected :: {result}")
        except Exception as exc:
            logger.exception(
                "Error occurred processing block",
                tracker="adv_boss_tracker",
                exc=exc,
            )
            raise exc
