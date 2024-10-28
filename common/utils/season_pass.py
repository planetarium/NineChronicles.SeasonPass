from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple

import jwt
from sqlalchemy import select, desc
from sqlalchemy.orm import joinedload

from common.enums import PassType
from common.models.season_pass import SeasonPass, Level


def get_current_season(sess, pass_type: PassType, include_exp: bool = False) -> Optional[SeasonPass]:
    now = datetime.now(tz=timezone.utc)
    stmt = select(SeasonPass).where(SeasonPass.pass_type == pass_type)
    if pass_type != PassType.WORLD_CLEAR_PASS:
        stmt = stmt.where(SeasonPass.start_timestamp <= now, SeasonPass.end_timestamp >= now)
    # DISCUSS: 월드 클리어 패스의 경우 여러개로 나뉘어서 순서대로 나와야 한다. 그래서 current pass 를 위 query 로 부르면 여러개가 나와야 한다.
    #  내 최신 월드 클리어 패스를 가져온 다음에 완료했으면 다음거로 넘어가도록 해야 되나?
    #  일단 1개만 있다고 가정하고 걍 들고오기로.
    if include_exp:
        stmt = stmt.options(joinedload(SeasonPass.exp_list))
    return sess.scalar(stmt)


def get_max_level(sess) -> Tuple[Level, int]:
    """
    Returns max level of season pass and repeating exp.
    Last one of level table is not a real level. Just for repeating reward.
    """
    # m1 for repeating level, m2 for real max level
    m1, m2 = sess.scalars(select(Level).order_by(desc(Level.level)).limit(2)).fetchall()
    return m2, abs(m1.exp - m2.exp)


def create_jwt_token(jwt_secret: str):
    iat = datetime.now(tz=timezone.utc)
    return jwt.encode({
        "iat": iat,
        "exp": iat + timedelta(minutes=1),
        "iss": "planetariumhq.com"
    }, jwt_secret)
