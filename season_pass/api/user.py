import json
import logging
from collections import defaultdict
from datetime import timezone, datetime
from uuid import uuid4

import boto3
from fastapi import APIRouter, Depends
from fastapi import HTTPException
from sqlalchemy import select, desc
from sqlalchemy.exc import IntegrityError

from common.enums import PlanetID
from common.models.season_pass import SeasonPass, Level
from common.models.user import UserSeasonPass, Claim
from common.utils.season_pass import get_current_season, get_max_level
from season_pass import settings
from season_pass.dependencies import session
from season_pass.exceptions import (SeasonNotFoundError, InvalidSeasonError, UserNotFoundError,
                                    InvalidUpgradeRequestError, )
from season_pass.schemas.user import (
    ClaimResultSchema, ClaimRequestSchema, UserSeasonPassSchema, UpgradeRequestSchema,
)
from season_pass.utils import verify_token

router = APIRouter(
    prefix="/user",
    tags=["User"],
)

sqs = boto3.client("sqs", region_name=settings.REGION_NAME)


@router.get("/status", response_model=UserSeasonPassSchema)
def user_status(season_id: int, avatar_addr: str, planet_id: str = "", sess=Depends(session)):
    avatar_addr = avatar_addr.lower()
    if not planet_id:
        planet_id = PlanetID.ODIN if settings.stage == "mainnet" else PlanetID.ODIN_INTERNAL
    else:
        try:
            planet_id = PlanetID(bytes(planet_id, "utf-8"))
        except Exception:
            raise HTTPException(status_code=400, detail=f"Invalid planet_id {planet_id}")

    target = sess.scalar(select(UserSeasonPass).where(
        UserSeasonPass.planet_id == planet_id,
        UserSeasonPass.season_pass_id == season_id,
        UserSeasonPass.avatar_addr == avatar_addr
    ))
    if not target:
        return UserSeasonPassSchema(planet_id=planet_id, avatar_addr=avatar_addr,
                                    season_pass_id=season_id)
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
            UserSeasonPass.planet_id == request.planet_id,
            UserSeasonPass.agent_addr == request.agent_addr,
            UserSeasonPass.avatar_addr == request.avatar_addr,
            UserSeasonPass.season_pass_id == request.season_id
        )
    )
    if not target_user:
        target_user = UserSeasonPass(
            planet_id=request.planet_id,
            agent_addr=request.agent_addr,
            avatar_addr=request.avatar_addr,
            season_pass_id=request.season_id,
        )
        sess.add(target_user)
        try:
            sess.commit()
            sess.refresh(target_user)
        except IntegrityError:
            logging.warning(f"{request.planet_id.value}::{request.avatar_addr}::{request.season_id} already exists.")
            sess.rollback()
            target_user = sess.scalar(
                select(UserSeasonPass)
                .where(
                    UserSeasonPass.planet_id == request.planet_id,
                    UserSeasonPass.agent_addr == request.agent_addr,
                    UserSeasonPass.avatar_addr == request.avatar_addr,
                    UserSeasonPass.season_pass_id == request.season_id
                )
            )

    # Not Premium and request only premium plus
    if not target_user.is_premium and not request.is_premium:
        raise InvalidUpgradeRequestError(
            f"Avatar {target_user.avatar_addr} is not in premium and doesn't request premium.")
    # Premium and not request premium plus
    if target_user.is_premium and not target_user.is_premium_plus and not request.is_premium_plus:
        raise InvalidUpgradeRequestError(
            f"Avatar {target_user.avatar_addr} is in premium and doesn't request premium plus.")
    # Request same or inclusive upgrade
    if (target_user.is_premium and request.is_premium) or (target_user.is_premium_plus and request.is_premium_plus):
        raise InvalidUpgradeRequestError(
            f"Avatar {target_user.avatar_addr} already purchased same or inclusive product. Duplicated purchase."
        )

    if request.is_premium:
        target_user.is_premium = request.is_premium
    if request.is_premium_plus:
        target_user.is_premium_plus = request.is_premium_plus
        target_user.exp += current_season.instant_exp
        target_user.level = sess.scalar(
            select(Level.level).where(Level.exp <= target_user.exp)
            .order_by(desc(Level.level)).limit(1)
        )

    if request.reward_list:
        # ClaimItems
        claim = Claim(
            uuid=str(uuid4()),
            planet_id=request.planet_id,
            agent_addr=request.agent_addr,
            avatar_addr=request.avatar_addr,
            reward_list=[{"ticker": x.ticker, "amount": x.amount, "decimal_places": x.decimal_places}
                         for x in request.reward_list],
        )
        sess.add(claim)
        sess.commit()
        sess.refresh(claim)

        resp = sqs.send_message(QueueUrl=settings.SQS_URL, MessageBody=json.dumps({"uuid": claim.uuid}))
        logging.debug(f"Message [{resp['MessageId']}] sent to SQS")

    sess.add(target_user)
    sess.commit()
    sess.refresh(target_user)
    return target_user


@router.post("/claim", response_model=ClaimResultSchema)
def claim_reward(request: ClaimRequestSchema, sess=Depends(session)):
    now = datetime.now(tz=timezone.utc)
    target_season = sess.scalar(select(SeasonPass).where(SeasonPass.id == request.season_id))
    if not request.force and not (target_season.start_timestamp <= now <= target_season.end_timestamp):
        # Return 404
        raise SeasonNotFoundError(f"Requested season {request.season_id} does not exist or not active.")

    user_season = sess.scalar(select(UserSeasonPass).where(
        UserSeasonPass.planet_id == request.planet_id,
        UserSeasonPass.avatar_addr == request.avatar_addr,
        UserSeasonPass.season_pass_id == target_season.id
    ))
    if not user_season:
        # No action executed about season pass.
        raise SeasonNotFoundError(
            f"No activity recorded for season {target_season.id} for avatar {request.avatar_addr}")

    available_rewards = user_season.available_rewards(sess)
    max_level, repeat_exp = get_max_level(sess)

    # calculate rewards to get
    reward_dict = {x["level"]: x for x in target_season.reward_list}
    target_reward_dict = defaultdict(int)
    for reward_level in available_rewards["normal"]:
        reward = reward_dict[reward_level]
        for item in reward["normal"]:
            target_reward_dict[(item["ticker"], item.get("decimal_places", 0))] += item["amount"]
    for reward_level in available_rewards["premium"]:
        reward = reward_dict[reward_level]
        for item in reward["premium"]:
            target_reward_dict[(item["ticker"], item.get("decimal_places", 0))] += item["amount"]

    claim = Claim(
        uuid=str(uuid4()),
        planet_id=user_season.planet_id,
        agent_addr=user_season.agent_addr.lower(),
        avatar_addr=user_season.avatar_addr.lower(),
        reward_list=[{"ticker": k[0], "decimal_places": k[1], "amount": v} for k, v in target_reward_dict.items()],
        normal_levels=available_rewards["normal"],
        premium_levels=available_rewards["premium"],
    )
    sess.add(claim)

    user_season.last_normal_claim = min(user_season.level, max_level.level)
    if user_season.is_premium:
        user_season.last_premium_claim = min(user_season.level, max_level.level)

    # Get repeating reward when user is above max level
    if user_season.level > max_level.level:
        user_season.exp -= repeat_exp * available_rewards["normal"].count(max_level.level + 1)
        user_season.level = max_level.level

    sess.add(user_season)
    sess.commit()
    sess.refresh(user_season)

    # Send message to SQS
    if (available_rewards["normal"] or available_rewards["premium"]) and settings.SQS_URL:
        resp = sqs.send_message(QueueUrl=settings.SQS_URL, MessageBody=json.dumps({"uuid": claim.uuid}))
        logging.debug(f"Message [{resp['MessageId']}] sent to SQS")

    # Return result
    return ClaimResultSchema(
        reward_list=[{"ticker": k[0], "decimal_places": k[1], "amount": v} for k, v in target_reward_dict.items()],
        user=user_season,
        # Deprecated: For backward compatibility
        items=[], currencies=[],
    )
