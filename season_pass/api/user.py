import json
import logging
from collections import defaultdict
from datetime import timezone, datetime
from uuid import uuid4

import boto3
from fastapi import APIRouter, Depends
from sqlalchemy import select

from common.models.season_pass import SeasonPass
from common.models.user import UserSeasonPass, Claim
from common.utils.season_pass import get_current_season, get_max_level
from season_pass import settings
from season_pass.dependencies import session
from season_pass.exceptions import (SeasonNotFoundError, InvalidSeasonError, UserNotFoundError,
                                    InvalidUpgradeRequestError, )
from season_pass.schemas.user import ClaimResultSchema, ClaimRequestSchema, UserSeasonPassSchema, UpgradeRequestSchema
from season_pass.utils import verify_token

router = APIRouter(
    prefix="/user",
    tags=["User"],
)

sqs = boto3.client("sqs", region_name=settings.REGION_NAME)


@router.get("/status", response_model=UserSeasonPassSchema)
def user_status(season_id: int, avatar_addr: str, sess=Depends(session)):
    target = sess.scalar(select(UserSeasonPass).where(
        UserSeasonPass.season_pass_id == season_id, UserSeasonPass.avatar_addr == avatar_addr
    ))
    if not target:
        return UserSeasonPassSchema(avatar_addr=avatar_addr, season_pass_id=season_id)

    return target


@router.post("/upgrade", response_model=UserSeasonPassSchema, dependencies=[Depends(verify_token)])
def upgrade_season_pass(request: UpgradeRequestSchema, sess=Depends(session)):
    """
    # Upgrade SeasonPass status to premium or premium plus
    ---
    **NOTE** : This API is server-to-server API between IAP and SeasonPass. Do not call it directly.

    Upgrade user's season pass status to premium(_plus) by purchasing IAP product.

    This API is not opened and should be verified using signed JWT. (See `verify_token` function for details.)

    """
    if not (request.is_premium or request.is_premium_plus):
        raise InvalidUpgradeRequestError(f"Neither premium nor premium_plus requested. Please request at least one.")

    current_season = get_current_season(sess)
    if request.season_id != current_season.id:
        raise InvalidSeasonError(f"Requested season {request.season_id} is not current season {current_season.id}")

    target_user = sess.scalar(
        select(UserSeasonPass)
        .where(
            UserSeasonPass.agent_addr == request.agent_addr,
            UserSeasonPass.avatar_addr == request.avatar_addr,
            UserSeasonPass.season_pass_id == request.season_id
        )
    )
    if not target_user:
        raise UserNotFoundError(f"Cannot found requested user data in season {request.season_id}")

    if not target_user.is_premium and not request.is_premium:
        raise InvalidUpgradeRequestError(
            f"Avatar {target_user.avatar_addr} is not in premium and doesn't request premium.")

    if target_user.is_premium and target_user.is_premium_plus and not request.is_premium_plus:
        raise InvalidUpgradeRequestError(
            f"Avatar {target_user.avatar_addr} is in premium and doesn't request premium plus.")

    if request.is_premium:
        target_user.is_premium = request.is_premium
    if request.is_premium_plus:
        target_user.is_premium_plus = request.is_premium_plus

    sess.add(target_user)
    sess.commit()
    sess.refresh(target_user)
    return target_user


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

    available_rewards = user_season.available_rewards(sess)
    if not (available_rewards["normal"] or available_rewards["premium"]):
        raise SeasonNotFoundError(f"No available rewards to get for avatar {user_season.avatar_addr}")

    max_level, repeat_exp = get_max_level(sess)

    # calculate rewards to get
    reward_items = defaultdict(int)
    reward_currencies = defaultdict(int)
    reward_dict = {x["level"]: x for x in target_season.reward_list}
    for reward_level in available_rewards["normal"]:
        reward = reward_dict[reward_level]
        for item in reward["normal"]["item"]:
            reward_items[item["id"]] += item["amount"]
        for curr in reward["normal"]["currency"]:
            reward_currencies[curr["ticker"]] += curr["amount"]
    for reward_level in available_rewards["premium"]:
        reward = reward_dict[reward_level]
        for item in reward["premium"]["item"]:
            reward_items[item["id"]] += item["amount"]
        for curr in reward["premium"]["currency"]:
            reward_currencies[curr["ticker"]] += curr["amount"]

    logging.debug(reward_items)
    logging.debug(reward_currencies)

    claim = Claim(
        uuid=str(uuid4()),
        agent_addr=user_season.agent_addr,
        avatar_addr=user_season.avatar_addr,
        reward_list={"item": reward_items, "currency": reward_currencies},
        normal_levels=available_rewards["normal"],
        premium_levels=available_rewards["premium"],
    )
    sess.add(claim)

    user_season.last_normal_claim = user_season.level
    if user_season.is_premium:
        user_season.last_premium_claim = user_season.level

    if user_season.level == max_level.level:
        user_season.exp -= repeat_exp * available_rewards["normal"].count(max_level.level + 1)

    sess.add(user_season)
    sess.commit()
    sess.refresh(user_season)

    # Send message to SQS
    if settings.SQS_URL:
        resp = sqs.send_message(QueueUrl=settings.SQS_URL, MessageBody=json.dumps({"uuid": claim.uuid}))
        logging.debug(f"Message [{resp['MessageId']}] sent to SQS")

    # Return result
    return ClaimResultSchema(
        items=[{"id": k, "amount": v} for k, v in reward_items.items()],
        currencies=[{"ticker": k, "amount": v} for k, v in reward_currencies.items()],
        user=user_season
    )
