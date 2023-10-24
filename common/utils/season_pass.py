from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select

from common.models.season_pass import SeasonPass


def get_current_season(sess) -> Optional[SeasonPass]:
    today = datetime.now(tz=timezone.utc).date()
    return sess.scalar(select(SeasonPass).where(SeasonPass.start_date <= today, SeasonPass.end_date >= today))
