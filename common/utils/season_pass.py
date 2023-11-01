from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import joinedload

from common.models.season_pass import SeasonPass


def get_current_season(sess, include_exp: bool = False) -> Optional[SeasonPass]:
    today = datetime.now(tz=timezone.utc).date()
    stmt = select(SeasonPass).where(SeasonPass.start_date <= today, SeasonPass.end_date >= today)
    if include_exp:
        stmt = stmt.options(joinedload(SeasonPass.exp_list))
    return sess.scalar(stmt)
