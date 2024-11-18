from typing import Dict, List

from sqlalchemy import select

from common.enums import PlanetID, ActionType
from common.models.action import ActionHistory, AdventureBossHistory
from common.models.season_pass import SeasonPass
from common.models.user import UserSeasonPass


def apply_exp(sess, planet_id: PlanetID, user_season_dict: Dict[str, UserSeasonPass], action_type: ActionType, exp: int,
              level_dict: Dict[int, int], block_index: int, action_data: List[Dict]):
    for d in action_data:
        target = user_season_dict[d["avatar_addr"]]
        target.exp += exp * d["count_base"]

        for lvl in sorted(level_dict.keys(), reverse=True):
            if target.exp >= level_dict[lvl]:
                target.level = lvl
                break

        sess.add(ActionHistory(
            planet_id=planet_id,
            block_index=block_index, tx_id=d.get("tx_id"),
            season_id=target.season_pass_id,
            agent_addr=target.agent_addr, avatar_addr=target.avatar_addr,
            action=action_type, count=d["count_base"], exp=exp * d["count_base"],
        ))


def verify_season_pass(sess, planet_id: PlanetID, current_season: SeasonPass, action_data: Dict[str, List]) \
        -> Dict[str, UserSeasonPass]:
    avatar_list = set()
    for data in action_data.values():
        for d in data:
            avatar_list.add(d["avatar_addr"])

    season_pass_dict = {x.avatar_addr: x for x in sess.scalars(
        select(UserSeasonPass)
        .where(
            UserSeasonPass.planet_id == planet_id,
            UserSeasonPass.season_pass_id == current_season.id,
            UserSeasonPass.avatar_addr.in_(avatar_list)
        )
    ).fetchall()}

    for data in action_data.values():
        for d in data:
            if d["avatar_addr"] not in season_pass_dict:
                new_season = UserSeasonPass(
                    planet_id=planet_id,
                    season_pass_id=current_season.id,
                    agent_addr=d["agent_addr"].lower(),
                    avatar_addr=d["avatar_addr"].lower(),
                    level=0, exp=0,
                )
                season_pass_dict[d["avatar_addr"]] = new_season
                sess.add(new_season)

    return season_pass_dict


def fetch_adv_boss_history(sess, planet_id: PlanetID, season: int, avatar_list: List[str]) -> dict[str, AdventureBossHistory]:
    return {
        h.avatar_addr: h for h in (
            sess.scalars(select(AdventureBossHistory).where(
                AdventureBossHistory.planet_id == planet_id,
                AdventureBossHistory.season == season,
                AdventureBossHistory.avatar_addr.in_(avatar_list)
            )).fetchall()
        )
    }
