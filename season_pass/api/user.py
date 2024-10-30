import json
import logging
from collections import defaultdict
from datetime import timezone, datetime, timedelta
from uuid import uuid4

import boto3
from fastapi import APIRouter, Depends
from fastapi import HTTPException
from sqlalchemy import select, desc
from sqlalchemy.exc import IntegrityError

from common.enums import PlanetID, PassType
from common.models.season_pass import SeasonPass, Level
from common.models.user import UserSeasonPass, Claim
from common.utils.season_pass import get_pass, get_max_level
from season_pass import settings
from season_pass.dependencies import session
from season_pass.exceptions import (SeasonNotFoundError, InvalidSeasonError, InvalidUpgradeRequestError,
                                    NotPremiumError, )
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
def user_status(planet_id: PlanetID, avatar_addr: str, pass_type: PassType, season_index: int, sess=Depends(session)):
    avatar_addr = avatar_addr.lower()
    target_pass = get_pass(sess, pass_type, season_index)
    if not target_pass:
        raise SeasonNotFoundError(f"Requested Season {pass_type}:{season_index} not exists.")

    target = sess.scalar(select(UserSeasonPass).where(
        UserSeasonPass.planet_id == planet_id,
        UserSeasonPass.season_pass_id == target_pass.id,
        UserSeasonPass.avatar_addr == avatar_addr
    ))
    if not target:
        return UserSeasonPassSchema(planet_id=planet_id, avatar_addr=avatar_addr, season_pass_id=target_pass.id)
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

    target_pass = get_pass(sess, request.pass_type, season_index=request.season_index, validate_current=True)
    if not target_pass:
        raise SeasonNotFoundError(f"[{request.planet_id}::{request.avatar_addr}]\n"
                                  f"Requested season {request.season_pass_id} not found or not current season.")
    target_usp = sess.scalar(
        select(UserSeasonPass)
        .where(
            UserSeasonPass.planet_id == request.planet_id,
            UserSeasonPass.agent_addr == request.agent_addr,
            UserSeasonPass.avatar_addr == request.avatar_addr,
            UserSeasonPass.season_pass_id == target_pass.id,
        )
    )
    if not target_usp:
        target_usp = UserSeasonPass(
            planet_id=request.planet_id,
            agent_addr=request.agent_addr,
            avatar_addr=request.avatar_addr,
            season_pass_id=target_pass.id,
        )
        sess.add(target_usp)
        try:
            sess.commit()
            sess.refresh(target_usp)
        except IntegrityError:
            logging.warning(f"{request.planet_id.value}::{request.avatar_addr}::{request.season_id} already exists.")
            sess.rollback()
            target_usp = sess.scalar(
                select(UserSeasonPass)
                .where(
                    UserSeasonPass.planet_id == request.planet_id,
                    UserSeasonPass.agent_addr == request.agent_addr,
                    UserSeasonPass.avatar_addr == request.avatar_addr,
                    UserSeasonPass.season_pass_id == target_pass.id,
                )
            )

    # Not Premium and request only premium plus
    if not target_usp.is_premium and not request.is_premium:
        raise InvalidUpgradeRequestError(
            f"Avatar {target_usp.avatar_addr} is not in premium and doesn't request premium.")
    # Premium and not request premium plus
    if target_usp.is_premium and not target_usp.is_premium_plus and not request.is_premium_plus:
        raise InvalidUpgradeRequestError(
            f"Avatar {target_usp.avatar_addr} is in premium and doesn't request premium plus.")
    # Request same or inclusive upgrade
    if (target_usp.is_premium and request.is_premium) or (target_usp.is_premium_plus and request.is_premium_plus):
        raise InvalidUpgradeRequestError(
            f"Avatar {target_usp.avatar_addr} already purchased same or inclusive product. Duplicated purchase."
        )

    if request.is_premium:
        target_usp.is_premium = request.is_premium
    if request.is_premium_plus:
        target_usp.is_premium_plus = request.is_premium_plus
        target_usp.exp += target_pass.instant_exp
        target_usp.level = sess.scalar(
            select(Level.level).where(Level.exp <= target_usp.exp)
            .order_by(desc(Level.level)).limit(1)
        )

    if request.reward_list:
        # ClaimItems
        claim = Claim(
            uuid=str(uuid4()),
            season_pass_id=target_usp.season_pass_id,
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

    sess.add(target_usp)
    sess.commit()
    sess.refresh(target_usp)
    return target_usp


def create_claim(sess, target_pass: SeasonPass, user_season: UserSeasonPass) -> Claim:
    available_rewards = user_season.available_rewards(sess)
    max_level, repeat_exp = get_max_level(sess)

    reward_dict = {x["level"]: x for x in target_pass.reward_list}
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
        season_pass_id=user_season.season_pass_id,
        planet_id=user_season.planet_id,
        agent_addr=user_season.agent_addr.lower(),
        avatar_addr=user_season.avatar_addr.lower(),
        reward_list=[{"ticker": k[0], "decimal_places": k[1], "amount": v} for k, v in target_reward_dict.items()],
        normal_levels=available_rewards["normal"],
        premium_levels=available_rewards["premium"],
    )
    user_season.last_normal_claim = min(user_season.level, max_level.level)
    if user_season.is_premium:
        user_season.last_premium_claim = min(user_season.level, max_level.level)

    # Get repeating reward when user is above max level
    if user_season.level > max_level.level:
        user_season.exp -= repeat_exp * available_rewards["normal"].count(max_level.level + 1)
        user_season.level = max_level.level

    sess.add(claim)
    sess.add(user_season)
    return claim


@router.post("/claim", response_model=ClaimResultSchema)
def claim_reward(request: ClaimRequestSchema, sess=Depends(session)):
    if request.force:
        target_pass = get_pass(sess, request.pass_type, request.season_index)
        if not target_pass:
            # Return 404
            raise SeasonNotFoundError(f"Requested season {request.pass_type}:{request.season_index} does not exist.")
    else:
        today = datetime.now(tz=timezone.utc).date()
        target_pass = get_pass(sess, request.pass_type, request.season_index, validate_current=True)
        if (target_pass.pass_type != PassType.WORLD_CLEAR_PASS
                and not (target_pass.start_date <= today <= target_pass.end_date)
        ):
            # Return 404
            raise SeasonNotFoundError(f"Requested season {request.season_pass_id} does not exist or not active.")

    user_season = sess.scalar(select(UserSeasonPass).where(
        UserSeasonPass.planet_id == request.planet_id,
        UserSeasonPass.avatar_addr == request.avatar_addr,
        UserSeasonPass.season_pass_id == target_pass.id
    ).with_for_update())
    if not user_season:
        # No action executed about season pass.
        raise SeasonNotFoundError(
            f"No activity recorded for season {target_pass.id} for avatar {request.avatar_addr}"
        )

    claim = create_claim(sess, target_pass, user_season)
    sess.commit()
    sess.refresh(user_season)

    # Send message to SQS
    if claim.reward_list and settings.SQS_URL:
        resp = sqs.send_message(QueueUrl=settings.SQS_URL, MessageBody=json.dumps({"uuid": claim.uuid}))
        logging.debug(f"Message [{resp['MessageId']}] sent to SQS")

    # Return result
    return ClaimResultSchema(
        user=user_season,
        reward_list=claim.reward_list,
    )


@router.post("/claim-prev", response_model=ClaimResultSchema)
def claim_prev_reward(request: ClaimRequestSchema, sess=Depends(session)):
    # Validation
    if request.pass_type == PassType.WORLD_CLEAR_PASS:
        raise InvalidSeasonError("You can claim WorldClearPass only with `/api/claim` API")
    if not request.prev:
        raise InvalidSeasonError("This API is only for prev. season. Please use `/api/claim` for current season.")

    target_pass = get_pass(sess, request.pass_type, request.season_index)
    if not target_pass:
        raise SeasonNotFoundError(f"Requested pass {request.pass_type}::{request.season_index} not found")

    now = datetime.now(tz=timezone.utc)
    if target_pass.end_timestamp >= now:
        raise InvalidSeasonError(f"Target season {target_pass.id} is not finished.")

    if target_pass.end_timestamp + timedelta(days=7) < now:
        raise InvalidSeasonError(f"Season {target_pass.id} finished over one week ago. Cannot claim.")

    user_season = sess.scalar(select(UserSeasonPass).where(
        UserSeasonPass.planet_id == request.planet_id,
        UserSeasonPass.season_pass_id == target_pass.id,
        UserSeasonPass.avatar_addr == request.avatar_addr
    ))
    if not user_season:
        raise SeasonNotFoundError(
            f"Season {target_pass.id} for avatar {request.avatar_addr} in planet {request.planet_id} not found")
    if not user_season.is_premium:
        raise NotPremiumError(f"Prev. season claim is only allowed for premium users.")

    # Claim
    claim = create_claim(sess, target_pass, user_season)
    sess.commit()
    sess.refresh(user_season)

    # Send message to SQS
    if claim.reward_list and settings.SQS_URL:
        resp = sqs.send_message(QueueUrl=settings.SQS_URL, MessageBody=json.dumps({"uuid": claim.uuid}))
        logging.debug(f"Message [{resp['MessageId']}] sent to SQS")

    # Return result
    return ClaimResultSchema(
        reward_list=claim.reward_list,
        user=user_season,
        # Deprecated: For backward compatibility
        items=[], currencies=[],
    )
