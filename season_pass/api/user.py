import logging
from collections import defaultdict
from datetime import timezone, datetime

from fastapi import APIRouter, Depends
from sqlalchemy import select

from common.models.season_pass import SeasonPass
from common.models.user import UserSeasonPass
from season_pass.dependencies import session
from season_pass.exceptions import SeasonNotFoundError
from season_pass.schemas.user import ClaimResultSchema, ClaimRequestSchema, UserSeasonPassSchema

router = APIRouter(
    prefix="/user",
    tags=["User"],
)


@router.get("/status", response_model=UserSeasonPassSchema)
def user_status(season_id: int, avatar_addr: str, sess=Depends(session)):
    target = sess.scalar(select(UserSeasonPass).where(
        UserSeasonPass.season_pass_id == season_id, UserSeasonPass.avatar_addr == avatar_addr
    ))
    if not target:
        return UserSeasonPassSchema(avatar_addr=avatar_addr, season_pass_id=season_id)

    return target


@router.post("/claim", response_model=ClaimResultSchema)
def claim_reward(request: ClaimRequestSchema, sess=Depends(session)):
    today = datetime.now(tz=timezone.utc).date()
    target_season = sess.scalar(select(SeasonPass).where(SeasonPass.id == request.season_id))
    if not (target_season.start_date <= today <= target_season.end_date):
        # Return 404
        raise SeasonNotFoundError(f"Requested season {request.season_id} does not exist or not active.")

    user_season = sess.scalar(select(UserSeasonPass).where(
        UserSeasonPass.avatar_addr == request.avatar_addr,
        UserSeasonPass.season_pass_id == target_season.id
    ))
    if not user_season:
        # No action executed about season pass.
        raise SeasonNotFoundError(
            f"No activity recorded for season {target_season.id} for avatar {user_season.avatar_addr}")

    available_rewards = user_season.available_rewards
    if not (available_rewards["normal"] or available_rewards["premium"]):
        raise SeasonNotFoundError(f"No available rewards to get for avatar {user_season.avatar_addr}")

    # calculate rewards to get
    reward_items = defaultdict(int)
    reward_currencies = defaultdict(int)
    for reward in target_season.reward_list:
        if reward["level"] in available_rewards["normal"]:
            for item in reward["normal"]["item"]:
                reward_items[item["id"]] += item["amount"]
            for curr in reward["normal"]["currency"]:
                reward_currencies[curr["ticker"]] += curr["amount"]
        if reward["level"] in available_rewards["premium"]:
            for item in reward["premium"]["item"]:
                reward_items[item["id"]] += item["amount"]
            for curr in reward["premium"]["currency"]:
                reward_currencies[curr["ticker"]] += curr["amount"]

    logging.debug(reward_items)
    logging.debug(reward_currencies)

    # Send message to SQS
    # TODO: Send Message to SQS

    user_season.last_normal_claim = user_season.level
    if user_season.is_premium:
        user_season.last_premium_claim = user_season.level

    sess.add(user_season)
    sess.commit()
    sess.refresh(user_season)

    # Return result
    return ClaimResultSchema(items=reward_items, currencies=reward_currencies, user=user_season)
