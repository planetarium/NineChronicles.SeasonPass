"""재스테이징(retry_stage)과 재서명(B2).

① restage: CREATED/INVALID claim 의 tx 를 stage 노드에 다시 보낸다(예전부터 하던 일).
②~④ 재서명(WORKER_RESIGN_MODE, 기본 off): 노드가 만료로 버린 tx 때문에 서명자 nonce 에 구멍이 생기면
  그 뒤 지급이 모두 멈춘다(2026-11-14·11-17 인터널, 사람이 nonce 를 비우고 검증자를 재시작해 풀었다).
  구멍을 쥔 claim 의 tx 를 **같은 nonce** 로 다시 서명해 사람 손 없이 메운다.
  순서는 ② → ① → ③④ 다. ② 에서 Bound 를 얻은 플래닛만 ① 이 만료 건을 건너뛴다.

안전성은 "tx 가 한 번이라도 나간 claim 의 nonce 는 바뀌지 않는다"와 (planet_id, nonce) 유일 제약에만
기댄다. 같은 서명자·같은 nonce 의 tx 는 블록에 하나만 들어가므로 한 claim 을 몇 번 다시 서명해도 지급은
최대 한 번이다. 나머지 판단(정지·뒤처짐·구멍 주인·상한·시간 상한)은 헛재서명과 운영 소음을 줄이는 장치다.
옛 tx 가 다른 노드에 남아 먼저 들어가면 tx 추적기가 claim_tx_history 로 그 tx 를 확정한다.
"""
import hashlib
import threading
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple, Union

import structlog
from app.celery_app import app
from app.config import config
from app.tasks.claim_task import configured_planet_ids
from app.utils.aws import RESIGN_KMS_CLIENT_CONFIG, Account
from app.utils.claim_tx import (
    ClaimTxFields,
    build_signed_claim_tx,
    build_unsigned_claim_tx,
    tx_signed_at,
)
from app.utils.gql import get_next_tx_nonce_once, get_tip_index_once, get_tx_status_once
from app.utils.nonce_lock import set_lock_timeout
from shared.enums import TxStatus
from shared.models.season_pass import SeasonPass
from shared.models.user import Claim, ClaimTxHistory
from shared.utils._graphql import GQLClient
from shared.utils.claim_tx_history import history_table_ready
from sqlalchemy import create_engine, func, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, scoped_session, sessionmaker

logger = structlog.get_logger(__name__)
engine = create_engine(str(config.pg_dsn), pool_size=5, max_overflow=5)

# 태스크 시작부터 이 시간이 지나면 재서명의 새 플래닛 판단·새 후보를 시작하지 않는다(claim_task 의
# RETRY_CLAIM_BUDGET_SEC 와 같은 이유). 판단용 조회는 단발(5초)이라 플래닛 판단 하나는 최악 15초(①
# restage 앞), 계획 직전 V 다시 읽기는 5초다. 후보 하나는 최악 약 67초다: 사전 확인 3회 15초 + KMS 서명
# 17초(RESIGN_KMS_CLIENT_CONFIG: 연결 3초 + 읽기 5초, 2번 시도, 재시도 대기 최대 1초) + 교체 잠금 5초 +
# stage 30초(GQLClient.stage: 3번 시도 × (연결 5초 + 읽기 5초), 스키마 미캐시면 더 붙는다).
# 110 + 67 = 177초가 claim-worker 종료 유예(180초)와 beat expires(240초) 안에 든다. 이 보장은 ① restage 가
# 110초 안에 끝난다는 전제 위에 있다(restage 자체엔 상한이 없다). 넘겨도 resign_one 은 어느 단계에서 끊겨도
# 행이 (옛 tx, N) 또는 (새 tx, N, CREATED) 로 남으므로 그 회차만 끊긴다.
RETRY_STAGE_BUDGET_SEC = 110
# 재서명 교체 UPDATE 의 행 잠금 대기 상한. consumer 는 claim 하나를 수 초 쥐고 추적기 쓰기는 짧다.
# 넘기면 그 후보만 raced 로 건너뛴다(다음 회차에 다시 본다).
RESIGN_LOCK_TIMEOUT = "5s"
# 다시 만든 unsigned tx 크기에 더하는 서명 몫(ECDSA DER 서명 + bencodex 키). 실제는 약 80바이트.
SIGNATURE_MARGIN_BYTES = 200
RESTAGE_LIMIT = 100
PENDING_STATUSES = (TxStatus.CREATED, TxStatus.INVALID, TxStatus.STAGED)
RESTAGE_STATUSES = (TxStatus.CREATED, TxStatus.INVALID)

# 정지·속도 판단용 관측(프로세스 메모리). 워커 재시작 뒤 첫 회차는 판단하지 않는다(first_observation).
_tip_since: Dict[bytes, Tuple[int, datetime]] = {}  # nonce 노드 tip, 그 tip 을 처음 본 시각
_last_obs: Dict[bytes, Tuple[int, int, datetime]] = {}  # 직전 (nonce 노드 tip, 기준 tip, 시각)
_not_ours_seen: Dict[bytes, int] = {}  # 직전 회차의 hole_not_ours V
_lookup_failures: Dict[bytes, int] = {}  # tip:·nonce: 보류가 이어진 회차 수
_no_reference_warned: set = set()

# tip:·nonce: 보류가 이만큼 이어지면 warning. 그동안 만료 건은 restage 되지만(노드는 받고 버린다)
# 재서명은 하지 않으므로, 구멍이 있으면 지급이 멈춘 채다.
LOOKUP_FAILURE_WARN_ROUNDS = 2

# 요약 로그를 warning 으로 남길 보류 사유. 나머지(nothing_pending·hole_fresh·first_observation·
# chain_stalled·no_reference·tip:…·nonce:…)는 info. hole_not_ours 는 같은 V 가 두 회차 이어질 때만,
# tip:…·nonce:… 는 LOOKUP_FAILURE_WARN_ROUNDS 회차 이어질 때만 warning.
WARN_REASONS = {
    "hole_manual_row",
    "hole_settled_mismatch",
    "hole_oversized",
    "hole_capped",
    "hole_unbuildable",
    "validator_lagging",
    "catching_up",
    "tip_regressed",
    "out_of_time",
}


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


def reset_resign_observations() -> None:
    """시험용: 프로세스 메모리의 관측을 비운다(워커 재시작과 같다)."""
    _tip_since.clear()
    _last_obs.clear()
    _not_ours_seen.clear()
    _lookup_failures.clear()
    _no_reference_warned.clear()


# --- KMS 계정(재서명 전용) ----------------------------------------------------------------------------

_resign_account: Optional[Account] = None
_resign_account_lock = threading.Lock()


def _get_resign_account() -> Account:
    """재서명 전용 KMS 계정. consumer 의 계정(claim_consumer._get_account)과 같은 키지만 클라이언트에
    짧은 시간 초과·재시도 상한을 둔다(RESIGN_KMS_CLIENT_CONFIG) — 회차 시간 상한 계산이 KMS 최악값에
    기댄다. consumer 경로의 클라이언트 설정은 바꾸지 않는다. 생성 실패는 캐시하지 않는다."""
    global _resign_account
    if _resign_account is None:
        with _resign_account_lock:
            if _resign_account is None:
                _resign_account = Account(
                    config.kms_key_id,
                    config.region_name,
                    client_config=RESIGN_KMS_CLIENT_CONFIG,
                )
    return _resign_account


# --- 주소 -------------------------------------------------------------------------------------


def _planet_key(planet_id: bytes) -> str:
    return bytes(planet_id).decode()


def stage_url(planet_id: bytes) -> Optional[str]:
    """tx 를 보내는 노드. 언제나 gql_url_map(시크릿 gql-url-dict)."""
    return config.gql_url_map.get(_planet_key(planet_id))


def nonce_url(planet_id: bytes) -> Optional[str]:
    """nonce·tip 을 읽는 노드(구멍의 기준). 없으면 stage 노드."""
    return config.nonce_gql_url_map.get(_planet_key(planet_id)) or stage_url(planet_id)


def reference_url(planet_id: bytes) -> Optional[str]:
    """nonce 노드가 뒤처졌는지 비교할 독립 노드. 없거나 nonce 노드와 같으면 None(재서명 안 함)."""
    ref = config.reference_gql_url_map.get(_planet_key(planet_id)) or stage_url(
        planet_id
    )
    if not ref or ref == nonce_url(planet_id):
        return None
    return ref


def resign_configured(planet_id: bytes) -> bool:
    """설정만 보고 정하는 정적 판정. 독립 기준 노드가 없는 플래닛은 재서명하지 않는다.
    만료 건 restage 를 건너뛸지는 이것이 아니라 그 회차에 Bound 를 얻었는지로 정한다."""
    return reference_url(planet_id) is not None


def _expired(signed_at: Optional[datetime], now: datetime) -> bool:
    """t 를 읽지 못한 tx 는 만료가 아닌 것으로 본다(건드리지 않음)."""
    if signed_at is None:
        return False
    return now - signed_at > timedelta(minutes=config.resign_after_minutes)


# --- 판단 결과 ---------------------------------------------------------------------------------


@dataclass
class Hold:
    """그 플래닛의 이번 회차 재서명을 멈춘다. ② 에서 나오면 ① 은 만료 건도 예전처럼 restage 한다."""

    reason: str
    warn: bool = False
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Bound:
    v: int  # nonce 노드의 nextTxNonce
    v_tip: int
    r_tip: int


@dataclass
class ResignTarget:
    """재서명 후보. ORM 객체를 세션 밖으로 들고 나가지 않으려고 값만 뽑는다."""

    id: int
    uuid: str
    planet_id: bytes
    nonce: int
    tx: str
    tx_id: str
    tx_status: TxStatus
    avatar_addr: str
    reward_list: List[Dict[str, Any]]
    normal_levels: List[int]
    premium_levels: List[int]
    pass_type: Optional[str]
    signed_at: Optional[datetime]

    def fields(self) -> ClaimTxFields:
        return ClaimTxFields(
            planet_id=self.planet_id,
            avatar_addr=self.avatar_addr,
            reward_list=self.reward_list,
            normal_levels=self.normal_levels,
            premium_levels=self.premium_levels,
            pass_type=self.pass_type,
        )


@dataclass
class Plan:
    bound: Bound
    targets: List[ResignTarget]


@dataclass
class Summary:
    """플래닛당 한 줄(Resign summary)."""

    planet_id: bytes
    mode: str
    reason: str = ""
    warn: bool = False
    bound: Optional[int] = None
    v_tip: Optional[int] = None
    r_tip: Optional[int] = None
    pending: Optional[int] = None
    hole_owner_status: Optional[str] = None
    resigned: List[int] = field(default_factory=list)
    would: List[int] = field(default_factory=list)
    settled: int = 0
    raced: int = 0
    fresh: int = 0
    oversized: int = 0
    unbuildable: int = 0
    untouched: Dict[str, Any] = field(default_factory=dict)
    out_of_time: int = 0
    stage_failed: bool = False
    restage_skipped_expired: int = 0
    extra: Dict[str, Any] = field(default_factory=dict)

    def hold(self, hold: Hold) -> None:
        self.reason = hold.reason
        self.warn = hold.warn or hold.reason in WARN_REASONS
        self.extra.update(hold.extra)

    def log(self, elapsed: float) -> None:
        kwargs = dict(
            planet_id=_planet_key(self.planet_id),
            mode=self.mode,
            reason=self.reason,
            bound=self.bound,
            v_tip=self.v_tip,
            r_tip=self.r_tip,
            pending=self.pending,
            hole_owner_status=self.hole_owner_status,
            resigned=self.resigned,
            would=self.would,
            settled=self.settled,
            raced=self.raced,
            fresh=self.fresh,
            oversized=self.oversized,
            unbuildable=self.unbuildable,
            untouched=self.untouched,
            out_of_time=self.out_of_time,
            stage_failed=self.stage_failed,
            restage_skipped_expired=self.restage_skipped_expired,
            elapsed=round(elapsed, 3),
            **self.extra,
        )
        if self.warn or self.out_of_time or self.stage_failed:
            logger.warning("Resign summary", **kwargs)
        else:
            logger.info("Resign summary", **kwargs)


class ResignStageFailed(Exception):
    pass


# --- ① restage --------------------------------------------------------------------------------


def _restage_planet(sess, gql, planet_id: bytes, now: datetime, skip_expired: bool):
    """한 플래닛의 CREATED/INVALID tx 를 nonce 순으로 다시 보낸다. (성공 여부, 건너뛴 만료 건수).

    조회 직후 필요한 값만 꺼내고 commit 해서, 결과가 비거나 중간에 멈춰도 열린 트랜잭션이 남지 않게
    한다(네트워크 호출 중 idle in transaction 이면 메인넷 역할의 60초 제한에 끊긴다).
    skip_expired(on 이고 이번 회차에 그 플래닛의 Bound 를 얻었을 때만): 만료된 tx 는 보내지 않는다 —
    노드는 "성공"을 돌려주지만 버린다. 이 건은 재서명 후보로 넘긴다. Bound 를 못 얻은 플래닛(조회 실패·
    정지·뒤처짐 등)은 재서명이 맡지 않으므로 예전처럼 만료 건도 보낸다."""
    rows = (
        sess.query(Claim.id, Claim.nonce, Claim.tx, Claim.tx_id)
        .filter(
            Claim.planet_id == planet_id,
            Claim.tx_status.in_(RESTAGE_STATUSES),
            Claim.created_at <= now - timedelta(minutes=5),
            Claim.reward_list != [],
            Claim.tx.isnot(None),
        )
        .order_by(Claim.nonce.asc())
        .limit(RESTAGE_LIMIT)
        .all()
    )
    rows = [(row.id, row.nonce, row.tx, row.tx_id) for row in rows]
    sess.commit()

    skipped = 0
    for claim_id, nonce, tx, tx_id in rows:
        if skip_expired and _expired(tx_signed_at(tx), now):
            skipped += 1
            continue
        try:
            success, msg, _ = gql.stage(planet_id, bytes.fromhex(tx))
        except Exception as e:
            success, msg = False, repr(e)
        if not success:
            # 같은 플래닛은 nonce 순서대로 들어가야 하므로 실패한 건 뒤를 보내 봐야 소용없다.
            logger.error(
                "Failed to stage tx", planet_id=planet_id, nonce=nonce, msg=msg
            )
            return False, skipped

        # 조건부로 적는다 — 그사이 추적기가 SUCCESS 로 바꿨거나 재서명이 tx 를 바꾼 행을 덮지 않게.
        sess.execute(
            update(Claim)
            .where(
                Claim.id == claim_id,
                Claim.tx_id == tx_id,
                Claim.tx_status.in_(RESTAGE_STATUSES),
            )
            .values(tx_status=TxStatus.STAGED)
            .execution_options(synchronize_session=False)
        )
        sess.commit()
    return True, skipped


# --- ② 기준 nonce ---------------------------------------------------------------------------------


def resign_bound(planet_id: bytes, now: datetime, address: str) -> Union[Bound, Hold]:
    """nonce 노드의 nextTxNonce(V)를 믿을 수 있으면 Bound. 네트워크만 쓰고 DB 는 쓰지 않는다.

    조회는 모두 단발이다. 예외·errors·−1 은 모두 "모름"이고 모름은 언제나 보류다."""
    n_url, r_url = nonce_url(planet_id), reference_url(planet_id)
    if r_url is None:
        return Hold("no_reference")
    jwt, timeout = config.headless_jwt_secret, config.gql_timeout
    try:
        v_tip = get_tip_index_once(n_url, jwt, timeout)
        r_tip = get_tip_index_once(r_url, jwt, timeout)
    except Exception as e:
        return Hold(f"tip:{type(e).__name__}")

    since = _tip_since.get(planet_id)
    if since is None or since[0] != v_tip:
        _tip_since[planet_id] = (v_tip, now)
    prev = _last_obs.get(planet_id)
    _last_obs[planet_id] = (v_tip, r_tip, now)
    tips = {"v_tip": v_tip, "r_tip": r_tip}
    if prev is None:
        return Hold("first_observation", extra=tips)
    if v_tip < prev[0]:
        return Hold("tip_regressed", extra=tips)  # 노드 교체·포크 복구 직후
    if (
        since is not None
        and since[0] == v_tip
        and now - since[1] >= timedelta(minutes=config.chain_stall_minutes)
    ):
        return Hold("chain_stalled", extra=tips)  # 정지 중 재서명은 끝없이 반복된다
    minutes = max((now - prev[2]).total_seconds() / 60, 1.0)
    if (v_tip - prev[0]) / minutes > config.validator_max_blocks_per_minute:
        return Hold("catching_up", extra=tips)  # 재시작 직후 따라잡는 중(시간 기준 속도)
    if (v_tip - prev[0]) - (r_tip - prev[1]) > config.validator_lag_blocks:
        return Hold("catching_up", extra=tips)  # 기준 노드보다 눈에 띄게 더 올라옴
    if r_tip - v_tip > config.validator_lag_blocks:
        return Hold("validator_lagging", extra=tips)
    try:
        v = get_next_tx_nonce_once(n_url, address, jwt, timeout)
    except Exception as e:
        return Hold(f"nonce:{type(e).__name__}", extra=tips)
    if not isinstance(v, int) or v < 0:
        return Hold("nonce:-1", extra=tips)
    return Bound(v=v, v_tip=v_tip, r_tip=r_tip)


# --- ③ 계획 -----------------------------------------------------------------------------------


def _target(claim: Claim, pass_type) -> ResignTarget:
    return ResignTarget(
        id=claim.id,
        uuid=claim.uuid,
        planet_id=claim.planet_id,
        nonce=claim.nonce,
        tx=claim.tx,
        tx_id=claim.tx_id,
        tx_status=claim.tx_status,
        avatar_addr=claim.avatar_addr,
        reward_list=claim.reward_list,
        normal_levels=list(claim.normal_levels or []),
        premium_levels=list(claim.premium_levels or []),
        pass_type=pass_type.value if pass_type is not None else None,
        signed_at=tx_signed_at(claim.tx),
    )


def _rebuilt_size(t: ResignTarget, account, now: datetime) -> Optional[int]:
    """지금 코드로 다시 만든 tx 의 크기(서명 몫 포함). 메모 압축(#325)으로 옛 tx 보다 작을 수 있다.
    만들 수 없으면(시즌패스 없는 예전 claim 등) None."""
    if t.pass_type is None:
        return None
    try:
        unsigned = build_unsigned_claim_tx(
            t.fields(), t.nonce, account.pubkey, account.address, now
        )
    except Exception:
        return None
    return len(unsigned) + SIGNATURE_MARGIN_BYTES


def _pending_filter(planet_id: bytes):
    return (
        Claim.planet_id == planet_id,
        Claim.tx_status.in_(PENDING_STATUSES),
        Claim.reward_list != [],
        Claim.tx.isnot(None),
    )


def plan_resigns(
    planet_id: bytes, bound: Bound, now: datetime, account, summary: Summary
) -> Union[Plan, Hold]:
    """기다리는 건 → 구멍 주인 → 후보. 짧은 읽기 세션에서 값만 꺼내고 바로 닫는다."""
    v = bound.v
    with Session(engine) as s:
        pending, untouched, low, high = s.execute(
            select(
                func.count().filter(Claim.nonce >= v),
                func.count().filter(Claim.nonce < v),
                func.min(Claim.nonce).filter(Claim.nonce < v),
                func.max(Claim.nonce).filter(Claim.nonce < v),
            ).where(*_pending_filter(planet_id))
        ).one()
        summary.pending = pending
        if untouched:
            summary.untouched = {"count": untouched, "min": low, "max": high}
        if pending == 0:
            # 평상시: V 는 "다음에 쓸 nonce" 라 그 주인 claim 이 아직 없다.
            _not_ours_seen.pop(planet_id, None)
            return Hold("nothing_pending")

        owner_row = s.execute(
            select(Claim, SeasonPass.pass_type)
            .outerjoin(SeasonPass, Claim.season_pass_id == SeasonPass.id)
            .where(Claim.planet_id == planet_id, Claim.nonce == v)
        ).first()
        if owner_row is None:
            # 소각 tx·DB 에 없는 tx 의 구멍이거나, 배정과 stage 사이의 짧은 틈. 같은 V 가 이어질 때만 경고.
            warn = _not_ours_seen.get(planet_id) == v
            _not_ours_seen[planet_id] = v
            return Hold("hole_not_ours", warn=warn)
        _not_ours_seen.pop(planet_id, None)

        owner_claim, owner_pass_type = owner_row
        summary.hole_owner_status = (
            owner_claim.tx_status.name if owner_claim.tx_status else None
        )
        if owner_claim.tx is None:
            return Hold("hole_manual_row")
        if owner_claim.tx_status in (TxStatus.SUCCESS, TxStatus.FAILURE):
            return Hold("hole_settled_mismatch")
        if owner_claim.tx_status not in PENDING_STATUSES:
            return Hold("hole_manual_row")
        owner = _target(owner_claim, owner_pass_type)

        window = timedelta(hours=config.resign_cap_window_hours)
        capped, oldest = s.execute(
            select(func.count(), func.min(ClaimTxHistory.created_at)).where(
                ClaimTxHistory.claim_id == owner.id,
                ClaimTxHistory.reason == "expired",
                ClaimTxHistory.nonce == ClaimTxHistory.bound_nonce,
                ClaimTxHistory.created_at > func.now() - window,
            )
        ).one()

        rows = s.execute(
            select(Claim, SeasonPass.pass_type)
            .outerjoin(SeasonPass, Claim.season_pass_id == SeasonPass.id)
            .where(*_pending_filter(planet_id), Claim.nonce >= v)
            .order_by(Claim.nonce.asc())
            .limit(config.resign_batch_limit)
        ).all()
        candidates = [_target(c, pt) for c, pt in rows]

    # 여기부터 DB 없이 판단한다.
    if not _expired(owner.signed_at, now):
        return Hold("hole_fresh")
    owner_size = _rebuilt_size(owner, account, now)
    if owner_size is None:
        return Hold("hole_unbuildable")
    if owner_size > config.resign_max_tx_bytes:
        return Hold(
            "hole_oversized",
            extra={"rebuilt_bytes": owner_size, "old_bytes": len(owner.tx) // 2},
        )
    if capped >= config.max_resigns:
        return Hold(
            "hole_capped",
            extra={
                "resigns_in_window": capped,
                "retry_after": (oldest + window).isoformat() if oldest else None,
            },
        )

    targets = []
    for t in candidates:
        if t.nonce == v:
            targets.append(owner)  # 위에서 모두 확인했다
            continue
        if not _expired(t.signed_at, now):
            summary.fresh += 1  # 아직 살아 있다
            continue
        size = _rebuilt_size(t, account, now)
        if size is None:
            summary.unbuildable += 1
            continue
        if size > config.resign_max_tx_bytes:
            summary.oversized += 1
            continue
        targets.append(t)
    if not targets or targets[0].nonce != v:
        # 주인을 확인한 직후 다른 경로가 바꿨다. 다음 회차에 다시 본다.
        return Hold("hole_owner_changed")
    return Plan(bound=bound, targets=targets)


# --- ④ 재서명 ---------------------------------------------------------------------------------


def _old_tx_urls(planet_id: bytes) -> List[str]:
    urls = []
    for url in (nonce_url(planet_id), reference_url(planet_id), stage_url(planet_id)):
        if url and url not in urls:
            urls.append(url)
    return urls


def old_tx_alive_or_unknown(planet_id: bytes, tx_id: str) -> bool:
    """옛 tx 가 어느 노드에서든 확정·포함·대기 중이거나 모르면 True(건드리지 않음).

    모든 노드가 INVALID 라고 할 때만 False. 노드당 한 번만 묻는다(재시도 없음).
    백스톱일 뿐이다 — 검증자의 INVALID 는 서명자 쿼터·내부 예외 때문에 "대기열에 없음"을 보장하지 않는다."""
    for url in _old_tx_urls(planet_id):
        try:
            status = get_tx_status_once(
                url, tx_id, config.headless_jwt_secret, config.gql_timeout
            )
        except Exception:
            return True
        if status != "INVALID":
            return True
    return False


def replace_tx_stmt(t: ResignTarget, new_tx_hex: str, new_tx_id: str):
    """claim 의 tx 를 조건부로 바꿔 끼운다. **nonce 는 SET 하지 않는다**(nonce 불변).

    WHERE 의 tx_id·nonce·상태가 계획 때와 같을 때만 바뀐다 — 그사이 추적기가 확정했거나 다른 경로가
    tx 를 바꾼 행은 rowcount 0 이다."""
    return (
        update(Claim)
        .where(
            Claim.id == t.id,
            Claim.tx_id == t.tx_id,
            Claim.nonce == t.nonce,
            Claim.tx_status.in_(PENDING_STATUSES),
        )
        .values(tx=new_tx_hex, tx_id=new_tx_id, tx_status=TxStatus.CREATED)
        .execution_options(synchronize_session=False)
    )


def resign_one(t: ResignTarget, bound: Bound, mode: str, account, gql) -> str:
    """서명 먼저, 교체·기록은 한 트랜잭션. "비운 상태"(tx NULL·nonce 있음)가 생기지 않는다.

    어느 단계에서 죽어도 행은 (옛 tx, N, 옛 상태) 또는 (새 tx, N, CREATED/STAGED) 다."""
    # 1) 옛 tx 확인(네트워크, DB 없음)
    if old_tx_alive_or_unknown(t.planet_id, t.tx_id):
        return "settled"
    if mode == "dry_run":
        return "would"

    # 2) 새 tx 서명(KMS, DB 없음). nonce 는 그대로, 시각은 지금.
    signed = build_signed_claim_tx(t.fields(), t.nonce, account, timestamp=_utcnow())
    new_tx_id = hashlib.sha256(signed).hexdigest()

    # 3) 조건부 교체 + 기록, 한 트랜잭션
    with Session(engine) as w:
        try:
            set_lock_timeout(w, RESIGN_LOCK_TIMEOUT)
            res = w.execute(replace_tx_stmt(t, signed.hex(), new_tx_id))
            if res.rowcount != 1:
                w.rollback()
                return "raced"  # 새 tx 는 stage 한 적 없으니 버려도 무해하다
            w.add(
                ClaimTxHistory(
                    claim_id=t.id,
                    planet_id=t.planet_id,
                    nonce=t.nonce,
                    tx_id=t.tx_id,
                    tx=t.tx,
                    signed_at=t.signed_at,
                    # 구멍 주인일 때만 V 를 적는다 — 상한은 구멍 재서명만 센다.
                    bound_nonce=bound.v if t.nonce == bound.v else None,
                    reason="expired",
                )
            )
            w.commit()
        except OperationalError as e:
            w.rollback()
            if getattr(e.orig, "pgcode", None) == "55P03":  # lock_not_available
                return "raced"
            raise

    # 4) stage(네트워크). 실패해도 행은 (새 tx, N, CREATED) 라 다음 회차 ① 이 다시 보낸다.
    try:
        ok, msg, _ = gql.stage(t.planet_id, signed)
    except Exception as e:
        ok, msg = False, repr(e)
    if not ok:
        raise ResignStageFailed(msg)

    # 5) STAGED 표시(짧은 쓰기, 새 tx_id 조건)
    with Session(engine) as w:
        w.execute(
            update(Claim)
            .where(
                Claim.id == t.id,
                Claim.tx_id == new_tx_id,
                Claim.tx_status == TxStatus.CREATED,
            )
            .values(tx_status=TxStatus.STAGED)
            .execution_options(synchronize_session=False)
        )
        w.commit()
    return "resigned"


def _count_lookup_failure(planet_id: bytes, verdict: Union[Bound, Hold]) -> None:
    """tip:·nonce: 보류가 이어지면 warning 으로 올린다. 다른 결과가 나오면 센 것을 지운다."""
    if isinstance(verdict, Hold) and verdict.reason.startswith(("tip:", "nonce:")):
        n = _lookup_failures.get(planet_id, 0) + 1
        _lookup_failures[planet_id] = n
        verdict.extra["lookup_failures"] = n
        if n >= LOOKUP_FAILURE_WARN_ROUNDS:
            verdict.warn = True
    else:
        _lookup_failures.pop(planet_id, None)


def _hold_with_tips(summary: Summary, hold: Hold) -> None:
    summary.v_tip = hold.extra.pop("v_tip", summary.v_tip)
    summary.r_tip = hold.extra.pop("r_tip", summary.r_tip)
    summary.hold(hold)


def _judge_planets(
    started: float, summaries: Dict[bytes, Summary]
) -> Tuple[Optional[Account], Dict[bytes, Bound]]:
    """② 플래닛마다 Bound 를 구한다. ① restage 보다 먼저 돈다 — 만료 건 restage 를 건너뛸지는 이번 회차에
    재서명이 그 플래닛을 실제로 맡을 수 있는지(Bound)로 정해야 하기 때문이다(정적 판정이면 조회가 실패한
    플래닛은 restage 도 재서명도 안 돼 조용히 멈춘다). 네트워크만 쓰고 DB 는 쓰지 않는다."""
    planets = list(summaries)
    try:
        account = _get_resign_account()
    except Exception as e:
        for p in planets:
            summaries[p].hold(Hold(f"account:{type(e).__name__}", warn=True))
        return None, {}

    bounds: Dict[bytes, Bound] = {}
    for p in planets:
        summary = summaries[p]
        if not resign_configured(p):
            if p not in _no_reference_warned:
                _no_reference_warned.add(p)
                logger.warning(
                    "Resign is not configured for planet; expired txs are restaged as before",
                    planet_id=_planet_key(p),
                )
            summary.hold(Hold("no_reference"))
            continue
        if time.monotonic() - started > RETRY_STAGE_BUDGET_SEC:
            summary.hold(Hold("out_of_time"))
            continue
        try:
            bound = resign_bound(p, _utcnow(), account.address)
        except Exception as e:
            logger.error(
                "Resign judgement failed", planet_id=_planet_key(p), exc_info=e
            )
            summary.hold(Hold(f"plan:{type(e).__name__}", warn=True))
            continue
        _count_lookup_failure(p, bound)
        if isinstance(bound, Hold):
            _hold_with_tips(summary, bound)
            continue
        summary.bound, summary.v_tip, summary.r_tip = bound.v, bound.v_tip, bound.r_tip
        bounds[p] = bound
    return account, bounds


def _refresh_bound(planet_id: bytes, bound: Bound, address: str) -> Union[Bound, Hold]:
    """① restage 동안 V 가 바뀌었을 수 있으니(consumer 의 새 claim, restage 가 메운 구멍) 계획 직전에 V 만
    다시 읽는다. 정지·뒤처짐 판단(tip)은 ② 의 것을 그대로 쓴다."""
    try:
        v = get_next_tx_nonce_once(
            nonce_url(planet_id),
            address,
            config.headless_jwt_secret,
            config.gql_timeout,
        )
    except Exception as e:
        return Hold(f"nonce:{type(e).__name__}")
    if not isinstance(v, int) or v < 0:
        return Hold("nonce:-1")
    return replace(bound, v=v)


def _resign_round(
    mode: str,
    gql,
    started: float,
    summaries: Dict[bytes, Summary],
    account,
    bounds: Dict[bytes, Bound],
) -> None:
    """③~⑤. 플래닛을 번갈아 처리하고 새 일을 시작하기 전마다 시간 상한을 본다(#329 와 같은 방식)."""

    def elapsed() -> float:
        return time.monotonic() - started

    plans: Dict[bytes, Plan] = {}
    for p, judged in bounds.items():
        summary = summaries[p]
        if elapsed() > RETRY_STAGE_BUDGET_SEC:
            summary.hold(Hold("out_of_time"))
            continue
        try:
            bound = _refresh_bound(p, judged, account.address)
            if isinstance(bound, Hold):
                _count_lookup_failure(p, bound)
                if summary.restage_skipped_expired:
                    # 만료 건 restage 를 건너뛴 회차라 이번엔 아무도 다시 보내지 않는다 — 바로 알린다.
                    bound.warn = True
                summary.hold(bound)
                continue
            if bound.v != judged.v:
                summary.extra["bound_before_restage"] = judged.v
            summary.bound = bound.v
            plan = plan_resigns(p, bound, _utcnow(), account, summary)
            if isinstance(plan, Hold):
                summary.hold(plan)
                continue
            plans[p] = plan
        except Exception as e:
            logger.error("Resign planning failed", planet_id=_planet_key(p), exc_info=e)
            summary.hold(Hold(f"plan:{type(e).__name__}", warn=True))

    stopped = set()
    out_of_time = False
    for i in range(config.resign_batch_limit):
        active = [
            p for p, plan in plans.items() if p not in stopped and i < len(plan.targets)
        ]
        if not active:
            break
        for p in active:
            if elapsed() > RETRY_STAGE_BUDGET_SEC:
                out_of_time = True
                break
            plan, summary = plans[p], summaries[p]
            t = plan.targets[i]
            try:
                result = resign_one(t, plan.bound, mode, account, gql)
            except ResignStageFailed as e:
                # 같은 플래닛은 nonce 순서대로 들어가야 하므로 남은 후보는 이번에 건너뛴다.
                summary.stage_failed = True
                summary.resigned.append(t.nonce)
                summary.extra["stage_error"] = str(e)[:200]
                stopped.add(p)
                continue
            except Exception as e:
                logger.error(
                    "Resign failed", planet_id=_planet_key(p), nonce=t.nonce, exc_info=e
                )
                summary.extra["error"] = type(e).__name__
                summary.warn = True
                stopped.add(p)
                continue
            if result == "resigned":
                summary.resigned.append(t.nonce)
            elif result == "would":
                summary.would.append(t.nonce)
            elif result == "settled":
                summary.settled += 1
            elif result == "raced":
                summary.raced += 1
            if t.nonce == plan.bound.v and result not in ("resigned", "would"):
                # 구멍 주인을 이번에 다시 서명하지 못했다(옛 tx 가 살아 있거나 다른 경로가 바꿈). 위쪽 후보는
                # 구멍이 메워지기 전엔 들어갈 수 없으니 이번 회차엔 손대지 않는다(헛서명·기록 행 낭비).
                summary.extra["owner_result"] = result
                stopped.add(p)
        if out_of_time:
            break

    if out_of_time:
        for p, plan in plans.items():
            if p in stopped:
                continue
            done = len(summaries[p].resigned) + len(summaries[p].would)
            done += summaries[p].settled + summaries[p].raced
            summaries[p].out_of_time = max(len(plan.targets) - done, 0)
            if summaries[p].out_of_time:
                summaries[p].hold(Hold("out_of_time"))


@app.task(
    name="season_pass.process_retry_stage",
    bind=True,
    max_retries=30,
    default_retry_delay=60,
    acks_late=True,
    retry_backoff=True,
    queue="claim_queue",
)
def process_retry_stage(self, message: Dict[str, Any] = None):
    """
    Retry stage transactions to the blockchain, then re-sign expired ones (B2, WORKER_RESIGN_MODE).

    Args:
        self: 태스크 인스턴스 (bind=True로 인해 자동으로 전달됨)
        message: send_to_worker에서 전달되는 메시지 (옵션)
    """
    started = time.monotonic()
    sess = scoped_session(sessionmaker(bind=engine))
    gql = GQLClient(
        config.converted_gql_url_map,
        config.headless_jwt_secret,
        timeout=config.gql_timeout,
    )

    mode = config.resign_mode
    if mode != "off" and not history_table_ready(engine):
        # 마이그레이션 전이거나 앱 역할에 권한이 없다. 재서명만 끄고 restage 는 지금처럼 한다.
        logger.warning("claim_tx_history not ready; resign disabled", resign_mode=mode)
        mode = "off"

    planets = configured_planet_ids()
    summaries = {p: Summary(planet_id=p, mode=mode) for p in planets}
    account, bounds = None, {}
    if mode != "off":
        # ② 를 ① 보다 먼저: 만료 건 restage 를 건너뛰는 것은 이번 회차에 Bound 를 얻은 플래닛뿐이다.
        try:
            account, bounds = _judge_planets(started, summaries)
        except Exception as e:
            logger.error("Resign judgement failed", exc_info=e)
            account, bounds = None, {}
    current_planet = None  # 예외 로그에 어느 플래닛에서 멈췄는지 남긴다
    try:
        now = _utcnow()
        # 플래닛마다 따로 고르고, 실패하면 그 플래닛의 남은 건만 이번 차례에서 건너뛴다(다음 beat 5분 뒤).
        # 예전엔 모든 플래닛을 nonce 순으로 100건 고르고 첫 실패에서 묶음 전체를 재시도해, 멈춘
        # 플래닛(2026-11-16 오딘 INVALID 51건)이 다른 플래닛의 재스테이징까지 막을 수 있었다.
        failed_planets = []
        for planet_id in planets:
            current_planet = planet_id
            ok, skipped = _restage_planet(
                sess,
                gql,
                planet_id,
                now,
                skip_expired=mode == "on" and planet_id in bounds,
            )
            summaries[planet_id].restage_skipped_expired = skipped
            if not ok:
                failed_planets.append(planet_id)
        if failed_planets:
            logger.error("Retry stage finished with failures", planets=failed_planets)
    except Exception as e:
        logger.error(
            "Error processing retry stage", planet_id=current_planet, exc_info=e
        )
        self.retry(exc=e)
        return
    finally:
        sess.close()

    if mode == "off":
        return
    # 재서명은 어떤 예외로도 self.retry 하지 않는다(restage 를 다시 돌릴 이유가 없다).
    try:
        if account is not None:
            _resign_round(mode, gql, started, summaries, account, bounds)
    except Exception as e:
        logger.error("Resign round failed", exc_info=e)
    elapsed = time.monotonic() - started
    for summary in summaries.values():
        summary.log(elapsed)
