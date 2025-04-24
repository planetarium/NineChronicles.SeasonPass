import logging
from datetime import datetime, timedelta, timezone

import requests
from app.config import config
from app.dependencies import session
from fastapi import APIRouter, Depends
from shared.constants import SEASONPASS_ADDRESS
from shared.enums import PassType, PlanetID, TxStatus
from shared.models.action import Block
from shared.models.user import Claim
from shared.utils.season_pass import create_jwt_token
from sqlalchemy import desc, func, or_, select
from sqlalchemy.orm import Session
from starlette.responses import JSONResponse

from . import admin, season_pass, tmp, user

__all__ = [
    season_pass,
    user,
    admin,
]

if config.stage != "mainnet":
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
        headers={
            "Authorization": f"Bearer {create_jwt_token(config.headless_jwt_secret)}"
        },
        timeout=2,
    )
    if resp.status_code != 200:
        return 0
    try:
        return resp.json()["data"]["nodeStatus"]["tip"]["index"]
    except Exception as e:
        logging.warning(f"Error occurred while getting {url}: {e}")
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
    is_mainnet = config.stage == "mainnet"
    if planet.lower() == "odin":
        planet_id = PlanetID.ODIN if is_mainnet else PlanetID.ODIN_INTERNAL
        url = config.converted_gql_url_map[planet_id]
    elif planet.lower() == "heimdall":
        planet_id = PlanetID.HEIMDALL if is_mainnet else PlanetID.HEIMDALL_INTERNAL
        url = config.converted_gql_url_map[planet_id]
    else:
        return JSONResponse(status_code=400, content=f"{planet} is not valid planet.")

    resp = requests.post(
        url,
        json={"query": f'query {{ nextTxNonce(address: "{SEASONPASS_ADDRESS}") }}'},
        headers={
            "Authorization": f"Bearer {create_jwt_token(config.headless_jwt_secret)}"
        },
    )
    result = resp.json()
    next_nonce = result["data"]["nextTxNonce"]

    highest_nonce = sess.scalar(
        select(Claim.nonce)
        .where(Claim.nonce.is_not(None), Claim.planet_id == planet_id)
        .order_by(desc(Claim.nonce))
        .limit(1)
    )
    logging.info(highest_nonce, next_nonce)

    if highest_nonce > next_nonce + 100:
        return JSONResponse(
            status_code=503,
            content=f"highest_nonce: {highest_nonce}, next_nonce: {next_nonce}",
        )

    return JSONResponse(status_code=200, content=result)


@router.get("/block-status")
def block_status(sess=Depends(session)):
    stage = config.stage
    result = {}

    odin_planet = PlanetID.ODIN if stage == "mainnet" else PlanetID.ODIN_INTERNAL
    odin_tip = get_tip(config.converted_gql_url_map[odin_planet])
    odin_blocks = get_db_tip(sess, odin_planet)
    result[odin_planet.name] = {k.value: odin_tip - v for k, v in odin_blocks.items()}

    heimdall_planet = (
        PlanetID.HEIMDALL if stage == "mainnet" else PlanetID.HEIMDALL_INTERNAL
    )
    heimdall_tip = get_tip(config.converted_gql_url_map[heimdall_planet])
    heimdall_blocks = get_db_tip(sess, heimdall_planet)
    result[heimdall_planet.name] = {
        k.value: heimdall_tip - v for k, v in heimdall_blocks.items()
    }

    err = False
    for planet, report in result.items():
        for pass_type, divergence in report.items():
            if abs(divergence) > 150:  # ~ 5min with 8 sec block internal
                err = True
                break

    return JSONResponse(status_code=503 if err else 200, content=result)


@router.get("/invalid-claim")
def invalid_claim(sess: Session = Depends(session)):
    now = datetime.now(tz=timezone.utc)
    invalid_claim_list = sess.scalars(
        select(Claim).where(
            Claim.created_at <= now - timedelta(minutes=5),
            Claim.reward_list != [],
            or_(Claim.tx_status != TxStatus.SUCCESS, Claim.tx_status.is_(None)),
        )
    ).fetchall()
    if invalid_claim_list:
        return JSONResponse(
            status_code=503,
            content=f"{len(invalid_claim_list)} of invalid claims found.",
        )
    return JSONResponse(status_code=200, content="No invalid claims found.")


@router.get("/failure-claim")
def failure_claim(sess: Session = Depends(session)):
    now = datetime.now(tz=timezone.utc)
    failure_claim_list = sess.scalars(
        select(Claim).where(
            Claim.reward_list != [], Claim.tx_status == TxStatus.FAILURE
        )
    ).fetchall()
    if failure_claim_list:
        return JSONResponse(
            status_code=503,
            content=f"{len(failure_claim_list)} of failure claims found.",
        )
    return JSONResponse(status_code=200, content="No failure claims found.")


@router.get("/balance/{planet}")
def balance(planet: str):
    if planet.lower() == "odin":
        url = config.converted_gql_url_map[PlanetID.ODIN]
    elif planet.lower() == "heimdall":
        url = config.converted_gql_url_map[PlanetID.HEIMDALL]
    else:
        return JSONResponse(status_code=400, content=f"{planet} is not valid planet.")
    resp = requests.post(
        url,
        json={
            "query": f"""query balanceQuery($address: Address! = \"{SEASONPASS_ADDRESS}\") {{
          stateQuery {{
              hourglass: balance(address: $address, currency: {{ticker: \"Item_NT_400000\", decimalPlaces: 0, minters: []}}) {{ currency {{ ticker }} quantity }}
              APPotion: balance(address: $address, currency: {{ticker: \"Item_NT_500000\", decimalPlaces: 0, minters: []}}) {{ currency {{ ticker }} quantity }}
              GoldenDust: balance(address: $address, currency: {{ticker: \"Item_NT_600201\", decimalPlaces: 0, minters: []}}) {{ currency {{ ticker }} quantity }}
              RubyDust: balance(address: $address, currency: {{ticker: \"Item_NT_600202\", decimalPlaces: 0, minters: []}}) {{ currency {{   ticker }} quantity }}
              EmeraldDust: balance(address: $address, currency: {{ticker: \"Item_NT_600203\", decimalPlaces: 0, minters: []}}) {{ currency {{   ticker }} quantity }}
              Scroll: balance(address: $address, currency: {{ticker: \"Item_T_600401\", decimalPlaces: 0, minters: []}}) {{ currency {{   ticker }} quantity }}
              SilverDust: balance(address: $address, currency: {{ticker: \"Item_NT_800201\", decimalPlaces: 0, minters: []}}) {{ currency {{   ticker }} quantity }}
              Crystal: balance(address: $address, currency: {{ticker: \"FAV__CRYSTAL\", decimalPlaces: 18, minters: []}}) {{ currency {{   ticker }} quantity }}
              GoldenLeaf: balance(address: $address, currency: {{ticker: \"FAV__RUNE_GOLDENLEAF\", decimalPlaces: 0, minters: []}}) {{ currency {{ ticker }} quantity }}
              CriRune: balance(address: $address, currency: {{ticker: \"FAV__RUNESTONE_CRI\", decimalPlaces: 0, minters: []}}) {{ currency {{ ticker }} quantity }}
              HPRune: balance(address: $address, currency: {{ticker: \"FAV__RUNESTONE_HP\", decimalPlaces: 0, minters: []}}) {{ currency {{ ticker }} quantity }}
              GoldenThor: balance(address: $address, currency: {{ticker: \"FAV__RUNESTONE_GOLDENTHOR\", decimalPlaces: 0, minters: []}}) {{ currency {{ ticker }} quantity }}
              Title: balance(address: $address, currency: {{ticker: \"Item_T_49900026\", decimalPlaces: 0, minters: []}}) {{ currency {{ ticker }} quantity }}
              Costume: balance(address: $address, currency: {{ticker: \"Item_T_40100032\", decimalPlaces: 0, minters: []}}) {{ currency {{ ticker }} quantity }}
          }}
        }}"""
        },
        headers={
            "Authorization": f"Bearer {create_jwt_token(config.headless_jwt_secret)}"
        },
    )
    data = resp.json()["data"]["stateQuery"]
    resp = {}
    for k, v in data.items():
        resp[k.lower()] = {
            "ticker": v["currency"]["ticker"],
            "amount": float(v["quantity"]),
        }

    return JSONResponse(status_code=200, content=resp)
