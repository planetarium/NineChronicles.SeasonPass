import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests
from fastapi import APIRouter, Depends
from shared.constants import SEASONPASS_ADDRESS
from shared.enums import PassType, PlanetID, TxStatus
from shared.models.action import Block
from shared.models.user import Claim
from shared.utils.season_pass import create_jwt_token
from sqlalchemy import desc, func, or_, select
from sqlalchemy.orm import Session
from starlette.responses import JSONResponse

from app.config import config
from app.dependencies import session

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


def get_tip(url) -> Optional[int]:
    """Tip index from one node, or None if it did not answer in time."""
    try:
        resp = requests.post(
            url,
            json={"query": "{ nodeStatus { tip { index } } }"},
            headers={
                "Authorization": f"Bearer {create_jwt_token(config.headless_jwt_secret)}"
            },
            timeout=config.tip_timeout,
        )
        if resp.status_code != 200:
            logging.warning(f"Tip from {url}: HTTP {resp.status_code}")
            return None
        return int(resp.json()["data"]["nodeStatus"]["tip"]["index"])
    except Exception as e:
        logging.warning(f"Error occurred while getting tip from {url}: {e}")
        return None


def tip_urls(planet: PlanetID) -> list[str]:
    urls = [config.converted_gql_url_map.get(planet)]
    urls += config.tip_fallback_url_map.get(planet.value.decode(), [])
    return list(dict.fromkeys(u for u in urls if u))


def get_best_tip(planet: PlanetID) -> Optional[int]:
    """Highest tip among the planet's nodes, asked in parallel. A node that is
    restarting is skipped; a node stuck behind is outvoted. None only when no
    node answers at all."""
    urls = tip_urls(planet)
    if not urls:
        return None
    with ThreadPoolExecutor(max_workers=len(urls)) as pool:
        tips = [t for t in pool.map(get_tip, urls) if t is not None]
    return max(tips) if tips else None


def get_db_tip(sess, planet_id: PlanetID) -> dict[PassType, int]:
    tips = sess.execute(
        select(Block.pass_type, func.max(Block.last_processed_index))
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
    elif planet.lower() == "thor":
        planet_id = PlanetID.THOR if is_mainnet else PlanetID.THOR_INTERNAL
        url = config.converted_gql_url_map[planet_id]
    else:
        return JSONResponse(status_code=400, content=f"{planet} is not valid planet.")

    resp = requests.post(
        url,
        json={"query": f'query {{ nextTxNonce(address: "{SEASONPASS_ADDRESS}") }}'},
        headers={
            "Authorization": f"Bearer {create_jwt_token(config.headless_jwt_secret)}"
        },
        timeout=config.gql_timeout,
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

    planets = [
        PlanetID.ODIN if stage == "mainnet" else PlanetID.ODIN_INTERNAL,
        PlanetID.HEIMDALL if stage == "mainnet" else PlanetID.HEIMDALL_INTERNAL,
    ]
    thor_planet = PlanetID.THOR if stage == "mainnet" else PlanetID.THOR_INTERNAL
    if (
        thor_planet in config.converted_gql_url_map
        and thor_planet.value.decode() in config.enabled_planets
    ):
        planets.append(thor_planet)

    err = False
    for planet in planets:
        tip = get_best_tip(planet)
        if tip is None:
            # Every node is down — a real outage, still alert, but say so
            # instead of reporting a tracker "lag" of minus the whole chain.
            result[planet.name] = {"error": "tip unavailable"}
            err = True
            continue
        report = {k.value: tip - v for k, v in get_db_tip(sess, planet).items()}
        result[planet.name] = report
        # Only a tracker trailing the tip is a problem. A tracker ahead of the
        # tip just means the node we asked is behind; that is not ours to page on.
        if any(lag > config.block_lag_threshold for lag in report.values()):
            err = True

    return JSONResponse(status_code=503 if err else 200, content=result)


@router.get("/invalid-claim", response_model=int)
def invalid_claim(sess: Session = Depends(session)):
    """Number of claims stuck unsettled: older than 5 minutes, with rewards, and
    not yet SUCCESS.

    Returns a COUNT (always HTTP 200) rather than 503-if-any, so the alert
    threshold lives in the monitor (mirrors IAP /purchase/invalid-receipt-count).
    A single transient slow-settling tx therefore no longer pages on its own.

    NOTE: the Assertible "Test Invalid Claim" check must assert on this number
    (fail when it exceeds the threshold). Otherwise the check always sees 200 and
    never alarms.
    """
    now = datetime.now(tz=timezone.utc)
    return sess.scalar(
        select(func.count())
        .select_from(Claim)
        .where(
            Claim.created_at <= now - timedelta(minutes=5),
            Claim.reward_list != [],
            or_(Claim.tx_status != TxStatus.SUCCESS, Claim.tx_status.is_(None)),
        )
    )


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
    elif planet.lower() == "thor":
        url = config.converted_gql_url_map[PlanetID.THOR]
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
        timeout=config.gql_timeout,
    )
    data = resp.json()["data"]["stateQuery"]
    resp = {}
    for k, v in data.items():
        resp[k.lower()] = {
            "ticker": v["currency"]["ticker"],
            "amount": float(v["quantity"]),
        }

    return JSONResponse(status_code=200, content=resp)
