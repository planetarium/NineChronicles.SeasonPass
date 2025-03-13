from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

import jwt
from sqlalchemy import and_, desc, or_, select
from sqlalchemy.orm import joinedload

from shared.enums import PassType
from shared.models.season_pass import Level, SeasonPass


def get_pass(
    sess,
    pass_type: PassType,
    season_index: int = None,
    validate_current: bool = False,
    include_exp: bool = False,
) -> Optional[SeasonPass]:
    stmt = select(SeasonPass).where(SeasonPass.pass_type == pass_type)

    if season_index is not None:
        stmt = stmt.where(SeasonPass.season_index == season_index)

    if validate_current:
        now = datetime.now(tz=timezone.utc)
        stmt = stmt.where(
            or_(  # match least one of following conditions
                # All time infinite
                and_(
                    SeasonPass.start_timestamp.is_(None),
                    SeasonPass.end_timestamp.is_(None),
                ),
                # Finite season with both start and end
                and_(
                    SeasonPass.start_timestamp.isnot(None),
                    SeasonPass.start_timestamp <= now,
                    SeasonPass.end_timestamp.isnot(None),
                    SeasonPass.end_timestamp >= now,
                ),
                # Infinite season with start
                and_(
                    SeasonPass.start_timestamp.isnot(None),
                    SeasonPass.start_timestamp <= now,
                    SeasonPass.end_timestamp.is_(None),
                ),
                # Finite season without start
                and_(
                    SeasonPass.start_timestamp.is_(None),
                    SeasonPass.end_timestamp.isnot(None),
                    SeasonPass.end_timestamp >= now,
                ),
            )
        )

    if include_exp:
        stmt = stmt.options(joinedload(SeasonPass.exp_list))

    return sess.scalar(stmt.order_by(desc(SeasonPass.id)))


def get_max_level(sess, pass_type: PassType) -> Tuple[Level, int]:
    """
    Returns max level of season pass and repeating exp.
    Last one of level table is not a real level. Just for repeating reward.
    """
    # World clear pass does not have repeating reward
    if pass_type == PassType.WORLD_CLEAR_PASS:
        max_level = sess.scalar(
            select(Level).where(Level.pass_type == pass_type).order_by(desc(Level.level))
        )
        return max_level, 0

    # m1 for repeating level, m2 for real max level
    m1, m2 = sess.scalars(
        select(Level)
        .where(Level.pass_type == pass_type)
        .order_by(desc(Level.level))
        .limit(2)
    ).fetchall()
    return m2, abs(m1.exp - m2.exp)


def get_level(sess, pass_type: PassType, exp: int) -> int:
    return (
        sess.scalar(
            select(Level.level)
            .where(Level.pass_type == pass_type, Level.exp <= exp)
            .order_by(desc(Level.level))
        )
        or 0
    )


def create_jwt_token(jwt_secret: str):
    iat = datetime.now(tz=timezone.utc)
    return jwt.encode(
        {"iat": iat, "exp": iat + timedelta(minutes=1), "iss": "planetariumhq.com"},
        jwt_secret,
    )
