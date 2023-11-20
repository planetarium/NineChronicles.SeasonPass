from datetime import datetime, timezone
from typing import Optional, Tuple

from sqlalchemy import select, desc
from sqlalchemy.orm import joinedload

from common.models.season_pass import SeasonPass, Level


def get_current_season(sess, include_exp: bool = False) -> Optional[SeasonPass]:
    today = datetime.now(tz=timezone.utc).date()
    stmt = select(SeasonPass).where(SeasonPass.start_date <= today, SeasonPass.end_date >= today)
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
