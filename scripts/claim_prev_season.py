from sys import argv

import requests
from sqlalchemy import create_engine, select, and_, or_
from sqlalchemy.orm import sessionmaker, scoped_session

from common.models.user import UserSeasonPass

DB_URI = ""
CLAIM_URL = "https://season-pass.9c.gg/api/user/claim"


def claim(season: int):
    """
    Force claim all unclaimed rewards for given season.
    Claim for active user first and inactive user later.

    ### Instruction
    1. SSH access to 9c mainnet bastion or SeasonPass EC2 instance
    2. Modify this file to set right DB_URI
    3. Run command `python -m scripts.claim_prev_season [season_id]`
    4. Check given season ID and enter `Y` to proceed.
    5. You can stop this script while running with `Ctrl + C` but processed individual claim cannot be undone.

    ### NOTICE
    This takes very long time to do and will trigger `Invalid Claim` PagerDuty alert.

    :param season: Target season to give unclaimed rewards.
    """
    engine = create_engine(DB_URI)
    sess = scoped_session(sessionmaker(bind=engine))

    try:
        claim_list = sess.scalars(select(UserSeasonPass).where(
            UserSeasonPass.season_pass_id == season,
            or_(UserSeasonPass.last_normal_claim < UserSeasonPass.level,
                and_(UserSeasonPass.is_premium, UserSeasonPass.last_premium_claim < UserSeasonPass.level)
                )
        )).fetchall()

        active_group = []
        inactive_group = []

        for claim in claim_list:
            if claim.last_normal_claim == 0:
                inactive_group.append(claim)
            else:
                active_group.append(claim)

        print(f"{len(claim_list)} claims to go : {len(active_group)} active group and {len(inactive_group)} inactive group.")

        for group in [active_group, inactive_group]:
            for i, c in enumerate(group):
                resp = requests.post(CLAIM_URL, json={"planet_id": c.planet_id.decode(),
                                                      "agent_addr": c.agent_addr, "avatar_addr": c.avatar_addr,
                                                      "season_id": c.season_pass_id, "force": True})
                print(f"{i + 1}/{len(claim_list)} :: {c.planet_id.decode()} :: {c.avatar_addr}")
                print(resp.json())
                print("=" * 32)
    finally:
        sess.close()


if __name__ == "__main__":
    try:
        target_season = int(argv[1])
    except:
        print(f"Usage: python claim_prev_season.py [season_id]")
    else:
        if input(f"Force claim all for season {target_season}? [y/N]") in ("y", "Y"):
            claim(int(argv[1]))
        else:
            print("Cancel claim. Exit.")

