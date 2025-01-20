import os
from datetime import datetime, timezone, timedelta

import requests
from fastapi import APIRouter, Depends
from sqlalchemy import select, or_, func
from sqlalchemy.orm import Session
from starlette.responses import JSONResponse

from common import SEASONPASS_ADDRESS, logger
from common.enums import TxStatus, PlanetID, PassType
from common.models.action import Block
from common.models.user import Claim
from common.utils.season_pass import create_jwt_token
from season_pass import settings
from season_pass.api import season_pass, user, tmp
from season_pass.dependencies import session

__all__ = [
    season_pass,
    user,
]

if os.environ.get("STAGE") != "mainnet":
    __all__.append(tmp)

router = APIRouter(
    prefix="/api",
    tags=["API"],
)

for view in __all__:
    router.include_router(view.router)


def get_tip(url) -> int:
    resp = requests.post(
        url,
        json={"query": "{ nodeStatus { tip { index } } }"},
        headers={"Authorization": f"Bearer {create_jwt_token(settings.HEADLESS_GQL_JWT_SECRET)}"},
        timeout=2
    )
    if resp.status_code != 200:
        return 0
    try:
        return resp.json()["data"]["nodeStatus"]["tip"]["index"]
    except Exception as e:
        logger.warning(f"Error occurred while getting {url}: {e}")
        return 0


def get_db_tip(sess, planet_id: PlanetID) -> dict[PassType, int]:
    tips = sess.execute(
        select(Block.pass_type, func.max(Block.index))
        .where(Block.planet_id == planet_id)
        .group_by(Block.pass_type)
    ).all()
    return {pass_type: index for pass_type, index in tips}


@router.get("/check-nonce")
def check_nonce(planet: str, sess=Depends(session)):
    if planet.lower() == "odin":
        url = os.environ.get("ODIN_GQL_URL")
    elif planet.lower() == "heimdall":
        url = os.environ.get("HEIMDALL_GQL_URL")
    elif planet.lower() == "thor":
        url = os.environ.get("THOR_GQL_URL")
    else:
        return JSONResponse(status_code=400, content=f"{planet} is not valid planet.")
    
    address = "0x0E19A992ad976B4986098813DfCd24B0775AC0AA"
    resp = requests.post(
        url,
        json={"query": f"{{ nextTxNonce(\"{address}\")}}"},
        headers={"Authorization": f"Bearer {create_jwt_token(settings.HEADLESS_GQL_JWT_SECRET)}"}
    )
    next_nonce = resp.json()["data"]

    highest_nonce = sess.scalar(select(Claim.nonce).order_by(Claim.nonce.desc())).limit(1)

    if (highest_nonce > next_nonce + 100):
        return JSONResponse(status_code=503, content=f"highest_nonce: {highest_nonce}, next_nonce: {next_nonce}")

    return JSONResponse(status_code=200, content=resp)


@router.get("/block-status")
def block_status(sess=Depends(session)):
    stage = os.environ.get("STAGE", "development")
    result = {}

    odin_planet = PlanetID.ODIN if stage == "mainnet" else PlanetID.ODIN_INTERNAL
    odin_tip = get_tip(os.environ.get("ODIN_GQL_URL"))
    odin_blocks = get_db_tip(sess, odin_planet)
    result[odin_planet.name] = {k.value: odin_tip - v for k, v in odin_blocks.items()}

    heimdall_planet = PlanetID.HEIMDALL if stage == "mainnet" else PlanetID.HEIMDALL_INTERNAL
    heimdall_tip = get_tip(os.environ.get("HEIMDALL_GQL_URL"))
    heimdall_blocks = get_db_tip(sess, heimdall_planet)
    result[heimdall_planet.name] = {k.value: heimdall_tip - v for k, v in heimdall_blocks.items()}

    thor_planet = PlanetID.THOR if stage == "mainnet" else PlanetID.THOR_INTERNAL
    thor_tip = get_tip(os.environ.get("THOR_GQL_URL"))
    thor_blocks = get_db_tip(sess, thor_planet)
    result[thor_planet.name] = {k.value: thor_tip - v for k, v in thor_blocks.items()}

    err = False
    for planet, report in result.items():
        for pass_type, divergence in report.items():
            if abs(divergence) > 35:  # ~ 5min with 8 sec block internal
                err = True
                break

    return JSONResponse(status_code=503 if err else 200, content=result)


@router.get("/invalid-claim")
def invalid_claim(sess: Session = Depends(session)):
    now = datetime.now(tz=timezone.utc)
    invalid_claim_list = sess.scalars(select(Claim).where(
        Claim.created_at <= now - timedelta(minutes=5),
        Claim.reward_list != [],
        or_(Claim.tx_status != TxStatus.SUCCESS, Claim.tx_status.is_(None))
    )).fetchall()
    if invalid_claim_list:
        return JSONResponse(status_code=503, content=f"{len(invalid_claim_list)} of invalid claims found.")
    return JSONResponse(status_code=200, content="No invalid claims found.")


@router.get("/failure-claim")
def failure_claim(sess: Session = Depends(session)):
    now = datetime.now(tz=timezone.utc)
    failure_claim_list = sess.scalars(select(Claim).where(
        Claim.reward_list != [],
        Claim.tx_status == TxStatus.FAILURE
    )).fetchall()
    if failure_claim_list:
        return JSONResponse(status_code=503, content=f"{len(failure_claim_list)} of failure claims found.")
    return JSONResponse(status_code=200, content="No failure claims found.")


@router.get("/balance/{planet}")
def balance(planet: str):
    if planet.lower() == "odin":
        url = os.environ.get("ODIN_GQL_URL")
    elif planet.lower() == "heimdall":
        url = os.environ.get("HEIMDALL_GQL_URL")
    elif planet.lower() == "thor":
        url = os.environ.get("THOR_GQL_URL")
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
              EmeraldDust: balance(address: $address, currency: {{ticker: \"Item_NT_600203\", decimalPlaces: 0, minters: []}}) {{ currency {{   ticker }} quantity }}
              Scroll: balance(address: $address, currency: {{ticker: \"Item_NT_600401\", decimalPlaces: 0, minters: []}}) {{ currency {{   ticker }} quantity }}
              SilverDust: balance(address: $address, currency: {{ticker: \"Item_NT_800201\", decimalPlaces: 0, minters: []}}) {{ currency {{   ticker }} quantity }}
              Crystal: balance(address: $address, currency: {{ticker: \"FAV__CRYSTAL\", decimalPlaces: 18, minters: []}}) {{ currency {{   ticker }} quantity }}
              GoldenLeaf: balance(address: $address, currency: {{ticker: \"FAV__RUNE_GOLDENLEAF\", decimalPlaces: 0, minters: []}}) {{ currency {{ ticker }} quantity }}
              CriRune: balance(address: $address, currency: {{ticker: \"FAV__RUNESTONE_CRI\", decimalPlaces: 0, minters: []}}) {{ currency {{ ticker }} quantity }}
              HPRune: balance(address: $address, currency: {{ticker: \"FAV__RUNESTONE_HP\", decimalPlaces: 0, minters: []}}) {{ currency {{ ticker }} quantity }}
              GoldenThor: balance(address: $address, currency: {{ticker: \"FAV__RUNESTONE_GOLDENTHOR\", decimalPlaces: 0, minters: []}}) {{ currency {{ ticker }} quantity }}
              Title: balance(address: $address, currency: {{ticker: \"Item_T_49900026\", decimalPlaces: 0, minters: []}}) {{ currency {{ ticker }} quantity }}
              Costume: balance(address: $address, currency: {{ticker: \"Item_T_40100032\", decimalPlaces: 0, minters: []}}) {{ currency {{ ticker }} quantity }}
          }}
        }}"""},
        headers={"Authorization": f"Bearer {create_jwt_token(settings.HEADLESS_GQL_JWT_SECRET)}"}
    )
    data = resp.json()["data"]["stateQuery"]
    resp = {}
    for k, v in data.items():
        resp[k.lower()] = {"ticker": v["currency"]["ticker"], "amount": float(v["quantity"])}

    return JSONResponse(status_code=200, content=resp)
