from typing import Dict, List

from common.enums import PlanetID, ActionType
from common.models.action import ActionHistory
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
            block_index=block_index, tx_id=d.get("tx_id", "0" * 64),
            season_id=target.season_pass_id,
            agent_addr=target.agent_addr, avatar_addr=target.avatar_addr,
            action=action_type, count=d["count_base"], exp=exp * d["count_base"],
        ))
