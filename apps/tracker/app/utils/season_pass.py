from typing import Dict, List

from shared.enums import ActionType, PlanetID
from shared.models.action import ActionHistory, AdventureBossHistory
from shared.models.season_pass import SeasonPass
from shared.models.user import UserSeasonPass
from sqlalchemy import select, text


def apply_exp(
    sess,
    planet_id: PlanetID,
    user_season_dict: Dict[str, UserSeasonPass],
    action_type: ActionType,
    exp: int,
    level_dict: Dict[int, int],
    block_index: int,
    action_data: List[Dict],
):
    for d in action_data:
        target = user_season_dict[d["avatar_addr"]]
        target.exp += exp * d["count_base"]

        for lvl in sorted(level_dict.keys(), reverse=True):
            if target.exp >= level_dict[lvl]:
                target.level = lvl
                break

        sess.add(
            ActionHistory(
                planet_id=planet_id,
                block_index=block_index,
                tx_id=d.get("tx_id"),
                season_id=target.season_pass_id,
                agent_addr=target.agent_addr,
                avatar_addr=target.avatar_addr,
                action=action_type,
                count=d["count_base"],
                exp=exp * d["count_base"],
            )
        )


# Upper bound on waiting for user_season_pass row locks held by the API (/claim,
# /upgrade, /claim-prev hold them for DB work only, so a normal wait is ms). On
# timeout the block fails and is retried on the next cycle.
USP_LOCK_TIMEOUT = "5s"


def verify_season_pass(
    sess,
    planet_id: PlanetID,
    current_season: SeasonPass,
    action_data: Dict[str, List],
    for_update: bool = False,
) -> Dict[str, UserSeasonPass]:
    """
    `for_update` locks the rows until commit, so exp is added to the latest value
    instead of overwriting what /claim (repeating reward deduction) or /upgrade
    (instant exp) committed meanwhile. Callers must finish all HTTP calls before
    this and commit right after applying exp: holding these locks across node/CDN
    round-trips makes /claim wait (the lock pile-up removed in #308).
    """
    avatar_list = set()
    for data in action_data.values():
        for d in data:
            avatar_list.add(d["avatar_addr"])

    stmt = select(UserSeasonPass).where(
        UserSeasonPass.planet_id == planet_id,
        UserSeasonPass.season_pass_id == current_season.id,
        UserSeasonPass.avatar_addr.in_(avatar_list),
    )
    if for_update:
        sess.execute(text(f"SET LOCAL lock_timeout = '{USP_LOCK_TIMEOUT}'"))
        # Fixed lock order so two tracker pods overlapping during a rollout can't deadlock.
        stmt = stmt.order_by(UserSeasonPass.id).with_for_update()

    season_pass_dict = {x.avatar_addr: x for x in sess.scalars(stmt).fetchall()}

    for data in action_data.values():
        for d in data:
            if d["avatar_addr"] not in season_pass_dict:
                new_season = UserSeasonPass(
                    planet_id=planet_id,
                    season_pass_id=current_season.id,
                    agent_addr=d["agent_addr"].lower(),
                    avatar_addr=d["avatar_addr"].lower(),
                    level=0,
                    exp=0,
                )
                season_pass_dict[d["avatar_addr"]] = new_season
                sess.add(new_season)

    return season_pass_dict


def fetch_adv_boss_history(
    sess, planet_id: PlanetID, season: int, avatar_list: List[str]
) -> dict[str, AdventureBossHistory]:
    return {
        h.avatar_addr: h
        for h in (
            sess.scalars(
                select(AdventureBossHistory).where(
                    AdventureBossHistory.planet_id == planet_id,
                    AdventureBossHistory.season == season,
                    AdventureBossHistory.avatar_addr.in_(avatar_list),
                )
            ).fetchall()
        )
    }
