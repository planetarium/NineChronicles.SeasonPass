import os
from datetime import datetime, timezone, timedelta

import requests
from fastapi import APIRouter, Depends
from sqlalchemy import select, or_, func
from sqlalchemy.orm import Session
from starlette.responses import JSONResponse

from common.enums import TxStatus
from common.models.action import Block
from common.models.user import Claim
from common.utils.season_pass import create_jwt_token
from season_pass import settings
from season_pass.api import season_pass, user, tmp
from season_pass.dependencies import session

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


@router.get("/block-status")
def block_status(sess=Depends(session)):
    resp = requests.post(
        os.environ["ODIN_GQL_URL"],
        json={"query": "{ nodeStatus { tip { index } } }"},
        headers={"Authorization": f"Bearer {create_jwt_token(settings.HEADLESS_GQL_JWT_SECRET)}"}
    )
    odin_tip = resp.json()["data"]["nodeStatus"]["tip"]["index"]
    resp = requests.post(
        os.environ["HEIMDALL_GQL_URL"],
        json={"query": "{ nodeStatus { tip { index } } }"},
        headers={"Authorization": f"Bearer {create_jwt_token(settings.HEADLESS_GQL_JWT_SECRET)}"}
    )
    heimdall_tip = resp.json()["data"]["nodeStatus"]["tip"]["index"]

    latest = (sess.query(Block.planet_id, func.max(Block.index))
              .group_by(Block.planet_id).order_by(Block.planet_id)
              ).all()

    err = abs(latest[0][1] - odin_tip) > 10 or abs(latest[1][1] - heimdall_tip) > 10
    msg = {
        latest[0][0].decode(): {
            "headless_tip": odin_tip,
            "db_tip": latest[0][1],
            "diverge": abs(odin_tip - latest[0][1]),
        },
        latest[1][0].decode(): {
            "headless_tip": heimdall_tip,
            "db_tip": latest[1][1],
            "diverge": abs(heimdall_tip - latest[1][1]),
        },
    }
    return JSONResponse(status_code=503 if err else 200, content=msg, )


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
