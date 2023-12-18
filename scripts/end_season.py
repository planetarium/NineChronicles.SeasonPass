from sys import argv

import requests
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker, scoped_session

from common import logger
from common.models.user import UserSeasonPass

DB_URI = ""
CLAIM_URL = ""

engine = create_engine(DB_URI)


def push_claim(season_id: int):
    sess = scoped_session(sessionmaker(bind=engine))
    all_season_pass = sess.scalars(select(UserSeasonPass).where(UserSeasonPass.season_pass_id == season_id)).fetchall()
    target_list = [x for x in all_season_pass if x.available_rewards]
    for target in target_list:
        resp = requests.post(CLAIM_URL, json={"planet_id": target.planet_id, "agent_addr": target.agent_addr,
                                              "avatar_addr": target.avatar_addr, "season_id": target.season_pass_id,
                                              "force": True})
        if resp.status_code != 200:
            logger.error(
                f"{target.planet_id} :: {target.season_id} :: {target.avatar_addr} Failed\n{resp.status_code} :: {resp.text}")
        else:
            logger.info(f"{target.planet_id} :: {target.season_id} :: {target.avatar_addr} Claimed")


if __name__ == "__main__":
    push_claim(int(argv[1]))
