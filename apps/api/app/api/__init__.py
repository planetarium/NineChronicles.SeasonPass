import logging
import threading
import time
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


def get_tip(url, token: str) -> Optional[int]:
    """Tip index from one node, or None if it did not answer in time."""
    try:
        resp = requests.post(
            url,
            json={"query": "{ nodeStatus { tip { index } } }"},
            headers={"Authorization": f"Bearer {token}"},
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


def get_best_tip(planet: PlanetID, token: str) -> Optional[int]:
    """Highest tip among the planet's nodes, asked in parallel. A node that is
    restarting is skipped; a node stuck behind is outvoted. None only when no
    node answers at all."""
    urls = tip_urls(planet)
    if not urls:
        return None
    with ThreadPoolExecutor(max_workers=len(urls)) as pool:
        tips = [t for t in pool.map(lambda u: get_tip(u, token), urls) if t is not None]
    return max(tips) if tips else None


def get_db_tip(sess, planet_id: PlanetID) -> dict[PassType, int]:
    tips = sess.execute(
        select(Block.pass_type, func.max(Block.last_processed_index))
        .where(Block.planet_id == planet_id)
        .group_by(Block.pass_type)
    ).all()
    return {pass_type: index for pass_type, index in tips}


class NonceUnavailable(Exception):
    """The nonce node gave no usable nextTxNonce."""

    def __init__(self, kind: str, detail: str = ""):
        super().__init__(detail or kind)
        self.kind = kind


def nonce_url(planet_id: PlanetID) -> Optional[str]:
    key = planet_id.value.decode()
    return config.nonce_gql_url_map.get(key) or config.gql_url_map.get(key)


def get_next_nonce(url: str) -> tuple[dict, int]:
    """(node body, nextTxNonce). Any failure — timeout, HTTP error, non-JSON, GQL error, no
    integer — raises NonceUnavailable or a requests exception."""
    resp = requests.post(
        url,
        json={"query": f'query {{ nextTxNonce(address: "{SEASONPASS_ADDRESS}") }}'},
        headers={
            "Authorization": f"Bearer {create_jwt_token(config.headless_jwt_secret)}"
        },
        timeout=config.gql_timeout,
    )
    if resp.status_code != 200:
        raise NonceUnavailable("HTTPError", f"HTTP {resp.status_code}")
    result = resp.json()
    next_nonce = (
        (result.get("data") or {}).get("nextTxNonce")
        if isinstance(result, dict)
        else None
    )
    if (
        not isinstance(result, dict)
        or result.get("errors")
        or not isinstance(next_nonce, int)
        or isinstance(next_nonce, bool)
    ):
        errors = result.get("errors") if isinstance(result, dict) else None
        raise NonceUnavailable("GraphQLError", str(errors or "no nextTxNonce"))
    return result, next_nonce


# Per planet, in this process. A restart (or another API worker process) starts from zero, so a
# 503 can come later than the configured time. Between two calls the state is not seen: a hole
# that closed and reopened at the same nextTxNonce is caught only by the highest nonce going down.
# _hole_seen: planet_id -> (next_nonce, highest_nonce, first seen at) while gap >= 1.
# _unavailable_seen: planet_id -> (first, last) failed call of the current failure streak. A
# failure after a quiet spell longer than UNAVAILABLE_STREAK_GAP_FACTOR × check_nonce_unavailable_minutes
# starts a new streak, so a process that missed the successes in between (another worker answered
# them) does not fire at once. Trade-off: a longer gap keeps an older failure in the streak; a gap
# equal to the alert time would let an outage seen by alternating processes never fire.
_hole_seen: dict[PlanetID, tuple[int, int, float]] = {}
_unavailable_seen: dict[PlanetID, tuple[float, float]] = {}
UNAVAILABLE_STREAK_GAP_FACTOR = 2
_state_lock = threading.Lock()  # sync handlers run on a thread pool
_now = time.monotonic


def stuck_seconds(
    planet_id: PlanetID,
    gap: Optional[int],
    next_nonce: int,
    highest_nonce: Optional[int],
) -> float:
    """How long the hole has stood with nextTxNonce not moving. 0 starts a new count: no hole,
    the node moved, or the DB's highest nonce went down (nonces cleared, e.g. a manual reset)."""
    now = _now()
    with _state_lock:
        if gap is None or gap < 1:
            _hole_seen.pop(planet_id, None)
            return 0
        seen = _hole_seen.get(planet_id)
        if seen is None or seen[0] != next_nonce or highest_nonce < seen[1]:
            _hole_seen[planet_id] = (next_nonce, highest_nonce, now)
            return 0
        return now - seen[2]


def unavailable_seconds(planet_id: PlanetID, failed: bool) -> float:
    """How long nonce lookups for the planet have kept failing (0 on the first failure)."""
    now = _now()
    with _state_lock:
        if not failed:
            _unavailable_seen.pop(planet_id, None)
            return 0
        first, last = _unavailable_seen.get(planet_id, (now, now))
        gap = (
            UNAVAILABLE_STREAK_GAP_FACTOR * config.check_nonce_unavailable_minutes * 60
        )
        if now - last > gap:
            first = now
        _unavailable_seen[planet_id] = (first, now)
        return now - first


def judge_nonce_gap(gap: int, stuck: float) -> tuple[int, str]:
    if gap > config.check_nonce_max_gap:
        return 503, "gap_too_large"
    if gap < -config.check_nonce_max_negative_gap:
        return 503, "chain_ahead_too_far"
    if gap >= 1 and stuck >= config.check_nonce_stuck_minutes * 60:
        return 503, "hole_stuck"
    if gap >= 1:
        return 200, "pending"
    if gap < 0:
        return 200, "chain_ahead"
    return 200, "ok"


@router.get("/check-nonce")
def check_nonce(planet: str, sess=Depends(session)):
    """Health check for the signer's nonces, read by an external monitor on the status code.

    gap = DB highest claim nonce − node nextTxNonce + 1: nonces the DB handed out that the
    node does not know yet. 0 when healthy. 503 when a hole stands without progress for
    check_nonce_stuck_minutes, when gap > check_nonce_max_gap, when the chain is more than
    check_nonce_max_negative_gap ahead, when the nonce node keeps failing for
    check_nonce_unavailable_minutes (200 "nonce_unavailable_transient" before that — a node
    restart alone must not fail the check), or when the planet has no node URL.
    The 200 body keeps the node's {"data": {"nextTxNonce": N}} and adds "check".
    """
    is_mainnet = config.stage == "mainnet"
    if planet.lower() == "odin":
        planet_id = PlanetID.ODIN if is_mainnet else PlanetID.ODIN_INTERNAL
    elif planet.lower() == "heimdall":
        planet_id = PlanetID.HEIMDALL if is_mainnet else PlanetID.HEIMDALL_INTERNAL
    elif planet.lower() == "thor":
        planet_id = PlanetID.THOR if is_mainnet else PlanetID.THOR_INTERNAL
    else:
        return JSONResponse(status_code=400, content=f"{planet} is not valid planet.")
    planet_key = planet_id.value.decode()
    check = {
        "planet": planet.lower(),
        "planet_id": planet_key,
        "max_gap": config.check_nonce_max_gap,
        "max_negative_gap": config.check_nonce_max_negative_gap,
        "stuck_minutes": config.check_nonce_stuck_minutes,
        "unavailable_minutes": config.check_nonce_unavailable_minutes,
    }

    url = nonce_url(planet_id)
    if not url:
        # Configuration, not the node: no waiting.
        logging.warning(
            "check-nonce planet_id=%s reason=nonce_unavailable error=NoNodeUrl",
            planet_key,
        )
        check.update(reason="nonce_unavailable", error="NoNodeUrl")
        return JSONResponse(status_code=503, content={"check": check})
    try:
        result, next_nonce = get_next_nonce(url)
    except (NonceUnavailable, requests.RequestException, ValueError) as e:
        error = e.kind if isinstance(e, NonceUnavailable) else type(e).__name__
        down = int(unavailable_seconds(planet_id, failed=True))
        if down >= config.check_nonce_unavailable_minutes * 60:
            status, reason = 503, "nonce_unavailable"
        else:
            status, reason = 200, "nonce_unavailable_transient"
        # The message may name the node; it stays in the log, never in the response.
        logging.warning(
            "check-nonce planet_id=%s reason=%s unavailable_seconds=%s error=%s: %s",
            planet_key,
            reason,
            down,
            error,
            e,
        )
        check.update(reason=reason, error=error, unavailable_seconds=down)
        return JSONResponse(status_code=status, content={"check": check})
    unavailable_seconds(planet_id, failed=False)

    highest_nonce = sess.scalar(
        select(Claim.nonce)
        .where(Claim.nonce.is_not(None), Claim.planet_id == planet_id)
        .order_by(desc(Claim.nonce))
        .limit(1)
    )
    gap = None if highest_nonce is None else highest_nonce - next_nonce + 1
    stuck = int(stuck_seconds(planet_id, gap, next_nonce, highest_nonce))
    if gap is None:
        status, reason = 200, "no_claims"
    else:
        status, reason = judge_nonce_gap(gap, stuck)

    log = logging.info if reason in ("ok", "no_claims") else logging.warning
    log(
        "check-nonce planet_id=%s highest_nonce=%s next_nonce=%s gap=%s "
        "stuck_seconds=%s reason=%s",
        planet_key,
        highest_nonce,
        next_nonce,
        gap,
        stuck,
        reason,
    )
    check.update(
        highest_nonce=highest_nonce,
        next_nonce=next_nonce,
        gap=gap,
        stuck_seconds=stuck,
        reason=reason,
    )
    return JSONResponse(status_code=status, content={**result, "check": check})


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

    # Outside the per-node try: a broken secret should 500, not look like "every node is down".
    token = create_jwt_token(config.headless_jwt_secret)
    err = False
    for planet in planets:
        tip = get_best_tip(planet, token)
        if tip is None:
            # Every node is down — a real outage, still alert, but say so
            # instead of reporting a tracker "lag" of minus the whole chain.
            result[planet.name] = {"error": "tip unavailable"}
            err = True
            continue
        report = {k.value: tip - v for k, v in get_db_tip(sess, planet).items()}
        result[planet.name] = report
        # Positive: the tracker trails the tip. Negative: EVERY node that answered
        # is behind what the tracker already processed (the tracker reads from the
        # primary, so it can't outrun it) — e.g. a node rolled back after a fork.
        # Both are real; only a single restarting node no longer trips this.
        if any(abs(lag) > config.block_lag_threshold for lag in report.values()):
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
