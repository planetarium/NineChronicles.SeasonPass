from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import select, or_
from sqlalchemy.orm import Session
from starlette.responses import JSONResponse

from dependencies import session
from enums import TxStatus
from models.user import Claim
from season_pass import settings
from season_pass.api import season_pass, user, tmp

router = APIRouter(
    prefix="/api",
    tags=["API"],
)

__all__ = [
    season_pass,
    user,
]

if settings.stage != "mainnet":
    __all__.append(tmp)

for view in __all__:
    router.include_router(view.router)


@router.get("/invalid-claim")
def invalid_claim(sess: Session = Depends(session)):
    now = datetime.now(tz=timezone.utc)
    invalid_claim_list = sess.scalars(select(Claim).where(
        Claim.created_at <= now - timedelta(minutes=3),
        Claim.reward_list != [],
        or_(Claim.tx_status != TxStatus.SUCCESS, Claim.tx_status.is_(None))
    )).fetchall()
    if invalid_claim_list:
        return JSONResponse(status_code=503, content=f"{len(invalid_claim_list)} of invalid claims found.")
    return JSONResponse(status_code=200, content="No invalid claims found.")
