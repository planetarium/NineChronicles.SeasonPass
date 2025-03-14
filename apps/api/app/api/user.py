import json
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import List
from uuid import uuid4

from fastapi import APIRouter, Depends
from shared.constants import CLAIM_QUEUE_NAME
from shared.enums import PassType, PlanetID, TxStatus
from shared.models.season_pass import SeasonPass
from shared.models.user import Claim, UserSeasonPass
from shared.utils._graphql import get_last_cleared_stage
from shared.utils.season_pass import get_level, get_max_level, get_pass
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from app.dependencies import rmq, session
from app.exceptions import (
    InvalidSeasonError,
    InvalidUpgradeRequestError,
    NotPremiumError,
    SeasonNotFoundError,
    ServerOverloadError,
)
from app.schemas.user import (
    ClaimRequestSchema,
    ClaimResultSchema,
    UpgradeRequestSchema,
    UserSeasonPassSchema,
)
from app.utils import verify_token

router = APIRouter(
    prefix="/user",
    tags=["User"],
)


def get_default_usp(
    sess,
    planet_id: PlanetID,
    agent_addr: str,
    avatar_addr: str,
    season_pass: SeasonPass,
) -> UserSeasonPass:
    match season_pass.pass_type:
        case PassType.WORLD_CLEAR_PASS:
            _, cleared_stage = get_last_cleared_stage(planet_id, avatar_addr, timeout=1)
            usp = UserSeasonPass(
                planet_id=planet_id,
                agent_addr=agent_addr,
                avatar_addr=avatar_addr,
                season_pass=season_pass,
                exp=cleared_stage,
            )
            if cleared_stage > 0:
                usp.level = get_level(sess, season_pass.pass_type, usp.exp)
            sess.add(usp)
            sess.commit()
            sess.refresh(usp)

        case PassType.COURAGE_PASS | PassType.ADVENTURE_BOSS_PASS:
            usp = UserSeasonPass(
                planet_id=planet_id,
                agent_addr=agent_addr,
                avatar_addr=avatar_addr,
                season_pass=season_pass,
                exp=0,
                level=0,
                is_premium=False,
                is_premium_plus=False,
                last_normal_claim=0,
                last_premium_claim=0,
            )

        case _:
            raise ValueError(
                f"{season_pass.pass_type.name} is not valid pass type to create default UserSeasonPass"
            )

    return usp


@router.get("/status", response_model=UserSeasonPassSchema)
def user_status(
    planet_id: str,
    agent_addr: str,
    avatar_addr: str,
    pass_type: PassType,
    season_index: int,
    sess=Depends(session),
):
    planet_id = PlanetID(bytes(planet_id, "utf-8"))
    agent_addr = agent_addr.lower()
    avatar_addr = avatar_addr.lower()
    target_pass = get_pass(sess, pass_type, season_index)
    if not target_pass:
        raise SeasonNotFoundError(
            f"Requested Season {pass_type}:{season_index} not exists."
        )

    now = datetime.now(tz=timezone.utc)
    if target_pass.start_timestamp and target_pass.start_timestamp > now:
        raise InvalidSeasonError(f"{pass_type}:{season_index} it not opened yet.")

    target = sess.scalar(
        select(UserSeasonPass).where(
            UserSeasonPass.planet_id == planet_id,
            UserSeasonPass.season_pass_id == target_pass.id,
            UserSeasonPass.avatar_addr == avatar_addr,
        )
    )
    if not target:
        target = get_default_usp(sess, planet_id, agent_addr, avatar_addr, target_pass)
    elif pass_type == PassType.WORLD_CLEAR_PASS and target.exp == 0:
        # 0 cleared stage is usually not normal data.
        _, cleared_stage = get_last_cleared_stage(
            planet_id, target.avatar_addr, timeout=1
        )
        target.exp = cleared_stage
        target.level = get_level(sess, target_pass.pass_type, target.exp)
        sess.add(target)
        sess.commit()

    return target


@router.get("/status/all", response_model=List[UserSeasonPassSchema])
def all_user_status(
    planet_id: str, agent_addr: str, avatar_addr: str, sess=Depends(session)
):
    planet_id = PlanetID(bytes(planet_id, "utf-8"))
    agent_addr = agent_addr.lower()
    avatar_addr = avatar_addr.lower()
    resp = []

    for pass_type in PassType:
        # Get current passes
        target_pass = get_pass(sess, pass_type, validate_current=True)
        if not target_pass:
            continue

        target = sess.scalar(
            select(UserSeasonPass).where(
                UserSeasonPass.planet_id == planet_id,
                UserSeasonPass.season_pass_id == target_pass.id,
                UserSeasonPass.avatar_addr == avatar_addr,
            )
        )
        if not target:
            target = get_default_usp(
                sess, planet_id, agent_addr, avatar_addr, target_pass
            )
        elif pass_type == PassType.WORLD_CLEAR_PASS and target.exp == 0:
            # 0 cleared stage is usually not normal data.
            _, cleared_stage = get_last_cleared_stage(
                planet_id, target.avatar_addr, timeout=1
            )
            target.exp = cleared_stage
            target.level = get_level(sess, target_pass.pass_type, target.exp)
            sess.add(target)
            sess.commit()

        resp.append(target)

        # Get prev. pass
        prev_pass = get_pass(sess, pass_type, season_index=target_pass.season_index - 1)
        if not prev_pass:
            continue

        prev = sess.scalar(
            select(UserSeasonPass).where(
                UserSeasonPass.planet_id == planet_id,
                UserSeasonPass.season_pass_id == prev_pass.id,
                UserSeasonPass.avatar_addr == avatar_addr,
            )
        )
        if not prev:
            prev = get_default_usp(sess, planet_id, agent_addr, avatar_addr, prev_pass)
        resp.append(prev)

    return resp


@router.post(
    "/upgrade",
    response_model=UserSeasonPassSchema,
    dependencies=[Depends(verify_token)],
)
def upgrade_season_pass(
    request: UpgradeRequestSchema, sess=Depends(session), rmq=Depends(rmq)
):
    """
    # Upgrade SeasonPass status to premium or premium plus
    ---
    **NOTE** : This API is server-to-server API between IAP and SeasonPass. Do not call it directly.

    Upgrade user's season pass status to premium by purchasing IAP product.

    This API is not opened and should be verified using signed JWT. (See `verify_token` function for details.)

    This API does not handle thor specific changes due to IAP sends modified reward value.
    """
    if not (request.is_premium or request.is_premium_plus):
        raise InvalidUpgradeRequestError(
            f"Neither premium nor premium_plus requested. Please request at least one."
        )

    target_pass = get_pass(
        sess,
        request.pass_type,
        season_index=request.season_index,
        validate_current=True,
    )
    if not target_pass:
        raise SeasonNotFoundError(
            f"[{request.planet_id}::{request.avatar_addr}]\n"
            f"Requested season {request.pass_type}:{request.season_index} not found or not current season."
        )
    target_usp = sess.scalar(
        select(UserSeasonPass).where(
            UserSeasonPass.planet_id == request.planet_id,
            UserSeasonPass.agent_addr == request.agent_addr,
            UserSeasonPass.avatar_addr == request.avatar_addr,
            UserSeasonPass.season_pass_id == target_pass.id,
        )
    )
    if not target_usp:
        target_usp = get_default_usp(
            sess,
            request.planet_id,
            request.agent_addr,
            request.avatar_addr,
            target_pass,
        )
        sess.add(target_usp)
        try:
            sess.commit()
            sess.refresh(target_usp)
        except IntegrityError:
            logging.warning(
                f"{request.planet_id.value}::{request.avatar_addr}::{target_pass.id} already exists."
            )
            sess.rollback()
            target_usp = sess.scalar(
                select(UserSeasonPass).where(
                    UserSeasonPass.planet_id == request.planet_id,
                    UserSeasonPass.agent_addr == request.agent_addr,
                    UserSeasonPass.avatar_addr == request.avatar_addr,
                    UserSeasonPass.season_pass_id == target_pass.id,
                )
            )

    # Not Premium and request only premium plus
    if not target_usp.is_premium and not request.is_premium:
        raise InvalidUpgradeRequestError(
            f"Avatar {target_usp.avatar_addr} is not in premium and doesn't request premium."
        )
    # Premium and not request premium plus
    if (
        target_usp.is_premium
        and not target_usp.is_premium_plus
        and not request.is_premium_plus
    ):
        raise InvalidUpgradeRequestError(
            f"Avatar {target_usp.avatar_addr} is in premium and doesn't request premium plus."
        )
    # Request same or inclusive upgrade
    if (target_usp.is_premium and request.is_premium) or (
        target_usp.is_premium_plus and request.is_premium_plus
    ):
        raise InvalidUpgradeRequestError(
            f"Avatar {target_usp.avatar_addr} already purchased same or inclusive product. Duplicated purchase."
        )

    if request.is_premium:
        target_usp.is_premium = request.is_premium
    if request.is_premium_plus:
        target_usp.is_premium_plus = request.is_premium_plus
        target_usp.exp += target_pass.instant_exp
        target_usp.level = get_level(sess, target_pass.pass_type, target_usp.exp)

    if request.reward_list:
        # ClaimItems
        claim = Claim(
            uuid=str(uuid4()),
            season_pass_id=target_usp.season_pass_id,
            planet_id=request.planet_id,
            agent_addr=request.agent_addr,
            avatar_addr=request.avatar_addr,
            # NOTE: reward_list is already modified from IAP service. Do not modify this.
            reward_list=[
                {
                    "ticker": x.ticker,
                    "amount": x.amount,
                    "decimal_places": x.decimal_places,
                }
                for x in request.reward_list
            ],
        )
        sess.add(claim)
        sess.commit()
        sess.refresh(claim)

        rmq.publish(
            routing_key=CLAIM_QUEUE_NAME,
            body={"uuid": claim.uuid},
        )
        logging.debug(f"Message for claim {claim.uuid} sent to queue")

    sess.add(target_usp)
    sess.commit()
    sess.refresh(target_usp)
    return target_usp


def create_claim(sess, target_pass: SeasonPass, user_season: UserSeasonPass) -> Claim:
    available_rewards = user_season.available_rewards(sess)
    max_level, repeat_exp = get_max_level(sess, target_pass.pass_type)
    reward_coef = 1
    if user_season.planet_id in (PlanetID.THOR, PlanetID.THOR_INTERNAL):
        reward_coef = 5

    reward_dict = {x["level"]: x for x in target_pass.reward_list}
    target_reward_dict = defaultdict(int)
    for reward_level in available_rewards["normal"]:
        reward = reward_dict[reward_level]
        for item in reward["normal"]:
            target_reward_dict[(item["ticker"], item.get("decimal_places", 0))] += (
                item["amount"] * reward_coef
            )
    for reward_level in available_rewards["premium"]:
        reward = reward_dict[reward_level]
        for item in reward["premium"]:
            target_reward_dict[(item["ticker"], item.get("decimal_places", 0))] += (
                item["amount"] * reward_coef
            )

    claim = Claim(
        uuid=str(uuid4()),
        season_pass_id=user_season.season_pass_id,
        planet_id=user_season.planet_id,
        agent_addr=user_season.agent_addr.lower(),
        avatar_addr=user_season.avatar_addr.lower(),
        reward_list=[
            {"ticker": k[0], "decimal_places": k[1], "amount": v}
            for k, v in target_reward_dict.items()
        ],
        normal_levels=available_rewards["normal"],
        premium_levels=available_rewards["premium"],
    )
    user_season.last_normal_claim = min(user_season.level, max_level.level)
    if user_season.is_premium:
        user_season.last_premium_claim = min(user_season.level, max_level.level)

    # Get repeating reward when user is above max level
    if user_season.level > max_level.level:
        user_season.exp -= repeat_exp * available_rewards["normal"].count(
            max_level.level + 1
        )
        user_season.level = max_level.level

    sess.add(claim)
    sess.add(user_season)
    return claim


@router.post("/claim", response_model=ClaimResultSchema)
def claim_reward(request: ClaimRequestSchema, sess=Depends(session), rmq=Depends(rmq)):
    if request.force:
        target_pass = get_pass(sess, request.pass_type, request.season_index)
        if not target_pass:
            # Return 404
            raise SeasonNotFoundError(
                f"Requested season {request.pass_type}:{request.season_index} does not exist."
            )
    else:
        today = datetime.now(tz=timezone.utc).date()
        target_pass = get_pass(
            sess, request.pass_type, request.season_index, validate_current=True
        )
        if target_pass.pass_type != PassType.WORLD_CLEAR_PASS and not (
            target_pass.start_date <= today <= target_pass.end_date
        ):
            # Return 404
            raise SeasonNotFoundError(
                f"Requested season {request.pass_type}:{request.season_index} does not exist or not active."
            )

    user_season = sess.scalar(
        select(UserSeasonPass)
        .where(
            UserSeasonPass.planet_id == request.planet_id,
            UserSeasonPass.avatar_addr == request.avatar_addr,
            UserSeasonPass.season_pass_id == target_pass.id,
        )
        .with_for_update()
    )
    if not user_season:
        # No action executed about season pass.
        raise SeasonNotFoundError(
            f"No activity recorded for season {target_pass.id} for avatar {request.avatar_addr}"
        )

    inprogress_claim_count = sess.scalar(
        select(func.count()).where(
            Claim.reward_list != [],
            or_(Claim.tx_status == TxStatus.STAGED, Claim.tx_status == TxStatus.INVALID),
        )
    )

    if inprogress_claim_count > 50:
        raise ServerOverloadError("NOTIFICATION_SEASONPASS_REWARD_CLAIMED_FAIL")

    claim = create_claim(sess, target_pass, user_season)
    sess.commit()
    sess.refresh(user_season)

    # Send message to SQS
    if claim.reward_list:
        rmq.publish(
            routing_key=CLAIM_QUEUE_NAME,
            body={"uuid": claim.uuid},
        )
        logging.debug(f"Message for claim {claim.uuid} sent to queue")

    # Return result
    return ClaimResultSchema(
        user=user_season,
        reward_list=claim.reward_list,
    )


@router.post("/claim-prev", response_model=ClaimResultSchema)
def claim_prev_reward(
    request: ClaimRequestSchema, sess=Depends(session), rmq=Depends(rmq)
):
    # Validation
    if request.pass_type == PassType.WORLD_CLEAR_PASS:
        raise InvalidSeasonError(
            "You can claim WorldClearPass only with `/api/claim` API"
        )
    if not request.prev:
        raise InvalidSeasonError(
            "This API is only for prev. season. Please use `/api/claim` for current season."
        )

    target_pass = get_pass(sess, request.pass_type, request.season_index)
    if not target_pass:
        raise SeasonNotFoundError(
            f"Requested pass {request.pass_type}::{request.season_index} not found"
        )

    now = datetime.now(tz=timezone.utc)
    if target_pass.end_timestamp >= now:
        raise InvalidSeasonError(
            f"Target season {target_pass.pass_type}:{target_pass.season_index} is not finished."
        )

    if target_pass.end_timestamp + timedelta(days=7) < now:
        raise InvalidSeasonError(
            f"Season {target_pass.pass_type}:{target_pass.season_index} finished over one week ago. Cannot claim."
        )

    user_season = sess.scalar(
        select(UserSeasonPass).where(
            UserSeasonPass.planet_id == request.planet_id,
            UserSeasonPass.season_pass_id == target_pass.id,
            UserSeasonPass.avatar_addr == request.avatar_addr,
        )
    )
    if not user_season:
        raise SeasonNotFoundError(
            f"Season {target_pass.pass_type}:{target_pass.season_index} for avatar {request.avatar_addr} "
            f"in planet {request.planet_id} not found"
        )
    if not user_season.is_premium:
        raise NotPremiumError(f"Prev. season claim is only allowed for premium users.")

    # Claim
    claim = create_claim(sess, target_pass, user_season)
    sess.commit()
    sess.refresh(user_season)

    # Send message to SQS
    if claim.reward_list:
        rmq.publish(
            routing_key=CLAIM_QUEUE_NAME,
            body={"uuid": claim.uuid},
        )
        logging.debug(f"Message for claim {claim.uuid} sent to queue")

    # Return result
    return ClaimResultSchema(
        reward_list=claim.reward_list,
        user=user_season,
        # Deprecated: For backward compatibility
        items=[],
        currencies=[],
    )
