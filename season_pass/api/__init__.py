import os
from datetime import datetime, timezone, timedelta

import requests
from fastapi import APIRouter, Depends
from sqlalchemy import select, or_, func
from sqlalchemy.orm import Session
from starlette.responses import JSONResponse

from common import SEASONPASS_ADDRESS
from common.enums import TxStatus, PlanetID
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
    stage = os.environ.get("STAGE", "development")
    resp = requests.post(
        os.environ["ODIN_GQL_URL"],
        json={"query": "{ nodeStatus { tip { index } } }"},
        headers={"Authorization": f"Bearer {create_jwt_token(settings.HEADLESS_GQL_JWT_SECRET)}"}
    )
    odin_tip = resp.json()["data"]["nodeStatus"]["tip"]["index"]
    odin_blocks = sess.scalars(
        select(Block.index)
        .where(Block.planet_id == (PlanetID.ODIN if stage == "mainnet" else PlanetID.ODIN_INTERNAL))
    ).fetchall()
    all_odin_blocks = set(range(min(odin_blocks), max(odin_blocks)))
    missing_odin_blocks = len(all_odin_blocks - set(odin_blocks))

    resp = requests.post(
        os.environ["HEIMDALL_GQL_URL"],
        json={"query": "{ nodeStatus { tip { index } } }"},
        headers={"Authorization": f"Bearer {create_jwt_token(settings.HEADLESS_GQL_JWT_SECRET)}"}
    )
    heimdall_tip = resp.json()["data"]["nodeStatus"]["tip"]["index"]
    heimdall_blocks = sess.scalars(
        select(Block.index)
        .where(Block.planet_id == (PlanetID.HEIMDALL if stage == "mainnet" else PlanetID.HEIMDALL_INTERNAL))
    ).fetchall()
    all_heimdall_blocks = set(range(min(heimdall_blocks), max(heimdall_blocks)))
    missing_heimdall_blocks = len(all_heimdall_blocks - set(heimdall_blocks))

    latest = (sess.query(Block.planet_id, func.max(Block.index))
              .group_by(Block.planet_id).order_by(Block.planet_id)
              ).all()

    err = (abs(latest[0][1] - odin_tip) > 10 or abs(latest[1][1] - heimdall_tip) > 10
           or missing_odin_blocks > 0 or missing_heimdall_blocks > 0)
    msg = {
        latest[0][0].decode(): {
            "headless_tip": odin_tip,
            "db_tip": latest[0][1],
            "diverge": abs(odin_tip - latest[0][1]),
            "missing": missing_odin_blocks,
        },
        latest[1][0].decode(): {
            "headless_tip": heimdall_tip,
            "db_tip": latest[1][1],
            "diverge": abs(heimdall_tip - latest[1][1]),
            "missing": missing_heimdall_blocks,
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


@router.get("/balance/{planet}")
def balance(planet: str):
    if planet.lower() == "odin":
        url = os.environ.get("ODIN_GQL_URL")
    elif planet.lower() == "heimdall":
        url = os.environ.get("HEIMDALL_GQL_URL")
    else:
        return JSONResponse(status_code=400, content=f"{planet} is not valid planet."
                            )
    resp = requests.post(
        url,
        json={"query": f"""query balanceQuery($address: Address! = \"{SEASONPASS_ADDRESS}\") {{
          stateQuery {{
              hourglass: balance(address: $address, currency: {{ticker: \"Item_NT_400000\", decimalPlaces: 0, minters: []}}) {{ currency {{ ticker }} quantity }}
              APPotion: balance(address: $address, currency: {{ticker: \"Item_NT_500000\", decimalPlaces: 0, minters: []}}) {{ currency {{ ticker }} quantity }}
              GoldenDust: balance(address: $address, currency: {{ticker: \"Item_NT_600201\", decimalPlaces: 0, minters: []}}) {{ currency {{ ticker }} quantity }}
              RubyDust: balance(address: $address, currency: {{ticker: \"Item_NT_600202\", decimalPlaces: 0, minters: []}}) {{ currency {{   ticker }} quantity }}
              SilverDust: balance(address: $address, currency: {{ticker: \"Item_NT_800201\", decimalPlaces: 0, minters: []}}) {{ currency {{   ticker }} quantity }}
              Crystal: balance(address: $address, currency: {{ticker: \"FAV__CRYSTAL\", decimalPlaces: 18, minters: []}}) {{ currency {{   ticker }} quantity }}
              GoldenLeaf: balance(address: $address, currency: {{ticker: \"FAV__RUNE_GOLDENLEAF\", decimalPlaces: 0, minters: []}}) {{ currency {{ ticker }} quantity }}
          }}
        }}"""},
        headers={"Authorization": f"Bearer {create_jwt_token(settings.HEADLESS_GQL_JWT_SECRET)}"}
    )
    data = resp.json()["data"]["stateQuery"]
    resp = {}
    for k, v in data.items():
        resp[k.lower()] = {"ticker": v["currency"]["ticker"], "amount": float(v["quantity"])}

    return JSONResponse(status_code=200, content=resp)
