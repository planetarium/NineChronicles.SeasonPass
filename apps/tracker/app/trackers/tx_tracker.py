import concurrent.futures
import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import NamedTuple, Optional

import structlog
from app.config import config
from gql.dsl import DSLQuery, dsl_gql
from gql.transport.exceptions import TransportQueryError
from shared.enums import PlanetID, TxStatus
from shared.models.user import Claim, ClaimTxHistory
from shared.utils._graphql import GQLClient
from shared.utils.claim_tx_history import history_table_ready
from shared.utils.transaction import tx_timestamp
from sqlalchemy import create_engine, func, insert, select, update
from sqlalchemy.orm import scoped_session, sessionmaker

logger = structlog.get_logger(__name__)

# Picked per planet (at most planets x PLANET_LIMIT per cycle). A single
# 200-row window ordered by id let a stalled planet's old claims fill it, so the
# other planets' txs were never looked up until those cleared.
PLANET_LIMIT = 100

engine = create_engine(str(config.pg_dsn), pool_size=5, max_overflow=5)

# 노드 tx 수명(메인넷 검증자·rpc 모두 10분). 이보다 오래 STAGING 인 건은 이상 신호라 따로 센다.
TX_LIFETIME = timedelta(minutes=10)
SETTLED = (TxStatus.SUCCESS, TxStatus.FAILURE)


class Lookup(NamedTuple):
    """tx 하나의 조회 결과. status 가 None 이면(resign_mode=on) 상태를 바꾸지 않고 updated_at 만 갱신한다."""

    tx_id: str
    status: Optional[TxStatus]
    msg: Optional[str]
    raw: Optional[str] = None  # 노드가 준 상태 이름(집계용)


def map_status(raw, mode: str) -> Optional[TxStatus]:
    """노드 상태 이름 → 적을 TxStatus.

    off·dry_run(지금 동작): TxStatus 에 없는 이름(STAGING·INCLUDED 등)은 INVALID.
    on: STAGING 은 STAGED(대기 중인 tx 를 INVALID 로 적으면 restage·재서명이 헛돈다), INCLUDED·모르는
      이름은 None(상태 유지). INCLUDED 는 txexec 를 지운 노드에서 영구히 나올 수 있다."""
    if mode == "on":
        if raw == "STAGING":
            return TxStatus.STAGED
        if raw == "INCLUDED":
            return None
        try:
            return TxStatus[raw]
        except (KeyError, TypeError):
            return None
    try:
        return TxStatus[raw]
    except (KeyError, TypeError):
        return TxStatus.INVALID


def process(planet_id: PlanetID, tx_id: str) -> Lookup:
    mode = config.resign_mode
    client = GQLClient(
        config.converted_gql_url_map,
        config.headless_jwt_secret,
        timeout=config.gql_timeout,
    )
    client.reset(planet_id)
    query = dsl_gql(
        DSLQuery(
            client.ds.StandaloneQuery.transaction.select(
                client.ds.TransactionHeadlessQuery.transactionResult.args(
                    txId=tx_id
                ).select(
                    client.ds.TxResultType.txStatus,
                    client.ds.TxResultType.blockIndex,
                    client.ds.TxResultType.blockHash,
                    client.ds.TxResultType.exceptionNames,
                )
            )
        )
    )
    try:
        resp = client.execute(query)
    except TransportQueryError as e:
        # 응답에 errors 가 있으면 gql 라이브러리가 이 예외를 던진다. off 는 지금처럼 조회 실패로 두고(쓰지
        # 않음), on 은 상태를 두고 updated_at 만 갱신해 그 건이 창 앞을 계속 차지하지 않게 한다.
        if mode == "on":
            return Lookup(tx_id, None, str(e)[:200], "errors")
        raise
    logger.debug(resp)

    if "errors" in resp:
        logger.error("GQL failed to get transaction status", errors=resp["errors"])
        if mode == "on":
            return Lookup(tx_id, None, json.dumps(resp["errors"]), "errors")
        return Lookup(tx_id, TxStatus.INVALID, json.dumps(resp["errors"]))

    result = resp["transaction"]["transactionResult"]
    raw = result["txStatus"]
    return Lookup(
        tx_id, map_status(raw, mode), json.dumps(result["exceptionNames"]), raw
    )


class _Target(NamedTuple):
    claim_id: int
    planet_id: PlanetID
    tx_id: str
    old_status: TxStatus
    nonce: Optional[int]
    tx: Optional[str]


def track_tx():
    logger.info("Tracking unfinished transactions")
    mode = config.resign_mode
    # 옛 tx 확정(재서명 기록)은 모드와 상관없이 켠다. 기록 행은 워커가 재서명했을 때만 생기므로 그 전
    # 데이터에는 영향이 없고, 끄면 off 로 되돌린 뒤 옛 tx 가 이긴 claim 이 영영 INVALID 로 남는다.
    use_history = history_table_ready(engine)

    # Read the unsettled claims in a short transaction, then release the
    # connection *before* the per-tx headless calls below. Previously this one
    # session stayed open across the whole GQL batch, so when a node was slow it
    # sat idle-in-transaction for minutes (observed 448s). That pinned the xmin
    # horizon and blocked autovacuum on claim/user_season_pass (dead tuples piled
    # up). Capture just the scalars we need, then close the session so the RPC
    # batch runs untethered to any DB transaction.
    read_sess = scoped_session(sessionmaker(bind=engine))
    history = defaultdict(list)  # claim_id -> [(tx_id, tx)] (reason='expired')
    try:
        targets = []
        for planet_id in config.converted_gql_url_map:
            # on: 조회한 건은 매번 updated_at 이 갱신되므로 updated_at 순으로 창이 돈다. id 순이면 영구
            # INVALID·INCLUDED 건 100개가 창 맨 앞을 영원히 차지한다.
            order = (Claim.updated_at, Claim.id) if mode == "on" else (Claim.id,)
            claim_list = read_sess.scalars(
                select(Claim)
                .where(
                    Claim.planet_id == planet_id.value,
                    Claim.tx_status.in_(
                        (
                            TxStatus.STAGED,
                            TxStatus.INVALID,
                        )
                    ),
                )
                .order_by(*order)
                .limit(PLANET_LIMIT)
            ).fetchall()
            targets.extend(
                _Target(
                    claim.id,
                    planet_id,
                    claim.tx_id,
                    claim.tx_status,
                    claim.nonce,
                    claim.tx,
                )
                for claim in claim_list
            )
        if use_history and targets:
            try:
                rows = read_sess.execute(
                    select(
                        ClaimTxHistory.claim_id, ClaimTxHistory.tx_id, ClaimTxHistory.tx
                    ).where(
                        ClaimTxHistory.claim_id.in_([t.claim_id for t in targets]),
                        ClaimTxHistory.reason == "expired",
                    )
                ).all()
                for row in rows:
                    history[row.claim_id].append((row.tx_id, row.tx))
            except Exception as e:
                read_sess.rollback()
                history.clear()
                logger.error("claim_tx_history read failed", exc_info=e)
    finally:
        read_sess.close()

    if not targets:
        logger.info("No transactions to track", tracker="tx_tracker")
        return

    # Query headless for each tx with no DB transaction held open. Each result is
    # taken on its own: one tx's exception (node timeout, GQL error) used to end
    # the whole cycle, so no other planet's status got written either. A failed
    # lookup leaves the claim's status as it is (never NULL or INVALID) and is
    # asked again next cycle. Earlier txs of re-signed claims (claim_tx_history)
    # are looked up the same way.
    result = defaultdict(list)  # (old_status, new_status) -> [tx_id]
    current = {}  # claim_id -> Lookup (absent when the lookup raised)
    wins = {}  # claim_id -> (tx_id, tx, status) of an earlier tx that made it in
    failures = defaultdict(lambda: defaultdict(int))  # planet_id -> {exc name: count}
    failure_sample = {}  # planet_id -> first error message, for diagnosis
    by_id = {t.claim_id: t for t in targets}
    futures = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        for t in targets:
            futures[executor.submit(process, t.planet_id, t.tx_id)] = (t, None)
            for hist_tx_id, hist_tx in history.get(t.claim_id, []):
                futures[executor.submit(process, t.planet_id, hist_tx_id)] = (
                    t,
                    hist_tx,
                )

        for future in concurrent.futures.as_completed(futures):
            t, hist_tx = futures[future]
            try:
                lookup = Lookup(*future.result())
            except Exception as e:
                name = type(e).__name__
                failures[t.planet_id][
                    name if hist_tx is None else f"history:{name}"
                ] += 1
                failure_sample.setdefault(t.planet_id, str(e)[:200])
                continue
            if hist_tx is None:
                current[t.claim_id] = lookup
            elif lookup.status in SETTLED:
                wins[t.claim_id] = (lookup.tx_id, hist_tx, lookup.status)
            # if msg:
            #     claim.msg = "\n".join([claim.msg, msg])

    for planet_id, errors in failures.items():
        logger.error(
            "Failed to track transactions",
            tracker="tx_tracker",
            planet_id=planet_id.value,
            failed=sum(errors.values()),
            errors=dict(errors),
            sample=failure_sample[planet_id],
        )

    logger.info(
        "Transactions found to track",
        tracker="tx_tracker",
        count=len(targets),
        planets={
            planet_id.value: count
            for planet_id, count in Counter(t.planet_id for t in targets).items()
        },
    )

    # 무엇을 쓸지 정한다. 같은 nonce 의 tx 는 블록에 하나만 들어가므로 현재 tx 와 기록 tx 가 둘 다
    # SUCCESS/FAILURE 일 수 없다.
    updates = []  # (claim_id, tx_id, new_status)
    touches = []  # (claim_id, tx_id): on 에서 상태를 두고 updated_at 만
    settles = []  # (target, (win_tx_id, win_tx, win_status))
    counts = defaultdict(Counter)  # planet_id -> resign-related counts
    now = datetime.now(tz=timezone.utc)
    for claim_id, t in by_id.items():
        cur = current.get(claim_id)
        if cur is not None and cur.status in SETTLED:
            updates.append((claim_id, cur.tx_id, cur.status))
            result[(t.old_status, cur.status)].append(cur.tx_id)
        elif claim_id in wins:
            settles.append((t, wins[claim_id]))
        elif cur is None:
            continue
        elif cur.status is None:
            touches.append((claim_id, cur.tx_id))
        else:
            updates.append((claim_id, cur.tx_id, cur.status))
            result[(t.old_status, cur.status)].append(cur.tx_id)
        if cur is not None and mode != "off":
            c = counts[t.planet_id.value]
            if cur.raw == "STAGING":
                c["staging"] += 1
                signed_at = tx_timestamp(t.tx)
                if signed_at is not None and now - signed_at > TX_LIFETIME:
                    c["staging_over_10m"] += 1
            elif cur.raw == "INCLUDED":
                c["included"] += 1
            elif cur.raw == "errors":
                c["errors"] += 1
            elif cur.raw is not None and cur.raw not in TxStatus.__members__:
                c["unknown"] += 1
            if mode == "dry_run" and cur.raw == "STAGING":
                c["would_staged"] += 1

    # Persist the resolved statuses in a fresh, short write transaction. The
    # read->write gap is wider now that the GQL batch runs outside the
    # transaction, so guard the write: match on tx_id (skip a claim re-staged
    # with a new tx in between) AND require the row still be STAGED/INVALID (skip
    # one already finalized to SUCCESS/FAILURE by another path), so we never
    # clobber a settled status with a stale GQL result.
    ops = (
        [(u[0], "status", u) for u in updates]
        + [(c[0], "touch", c) for c in touches]
        + [(s[0].claim_id, "settle", s) for s in settles]
    )
    write_sess = scoped_session(sessionmaker(bind=engine))
    try:
        # claim id 순으로 쓴다 — 배포 중 tx_tracker 둘이 겹쳐도 서로 다른 순서로 행을 잠가 교착하지 않게.
        for claim_id, kind, op in sorted(ops, key=lambda o: o[0]):
            if kind == "status":
                _, tx_id, tx_status = op
                write_sess.execute(
                    update(Claim)
                    .where(
                        Claim.id == claim_id,
                        Claim.tx_id == tx_id,
                        Claim.tx_status.in_((TxStatus.STAGED, TxStatus.INVALID)),
                    )
                    .values(tx_status=tx_status)
                )
            elif kind == "touch":
                _, tx_id = op
                write_sess.execute(touch_stmt(claim_id, tx_id))
            else:
                t, (win_tx_id, win_tx, win_status) = op
                res = write_sess.execute(
                    settle_stmt(claim_id, t.tx_id, win_tx_id, win_tx, win_status)
                )
                if res.rowcount == 1:
                    # 밀려난 tx 도 남긴다(consumer 의 nonce 재배정 차단 근거, 추적기는 다시 보지 않는다).
                    write_sess.execute(
                        insert(ClaimTxHistory).values(
                            claim_id=claim_id,
                            planet_id=t.planet_id.value,
                            nonce=t.nonce,
                            tx_id=t.tx_id,
                            tx=t.tx,
                            signed_at=tx_timestamp(t.tx),
                            bound_nonce=None,
                            reason="lost",
                        )
                    )
                    logger.warning(
                        "Claim settled by earlier tx",
                        tracker="tx_tracker",
                        planet_id=t.planet_id.value,
                        nonce=t.nonce,
                        tx_id=win_tx_id,
                        status=win_status.name,
                        lost=t.tx_id,
                    )
                    result[(t.old_status, win_status)].append(win_tx_id)
        write_sess.commit()
    finally:
        write_sess.close()

    if mode != "off" or settles:
        logger.info(
            "Tx tracker resign summary",
            tracker="tx_tracker",
            mode=mode,
            touched=len(touches),
            settled_by_history=len(settles),
            planets={p: dict(c) for p, c in counts.items()},
        )

    # Keys used to be status-name strings compared against None/TxStatus, which
    # never matched, so unchanged claims were logged as "changed" every cycle.
    for (old_status, status), tx_list in result.items():
        if status == old_status:
            logger.info(f"{len(tx_list)} transactions are still {status.name}")
        else:
            logger.info(f"{len(tx_list)} transactions are changed to {status.name}")


def touch_stmt(claim_id: int, tx_id: str):
    """on: 상태는 두고 updated_at 만 갱신해 updated_at 순 창의 뒤로 보낸다."""
    return (
        update(Claim)
        .where(
            Claim.id == claim_id,
            Claim.tx_id == tx_id,
            Claim.tx_status.in_((TxStatus.STAGED, TxStatus.INVALID)),
        )
        .values(updated_at=func.now())
    )


def settle_stmt(claim_id: int, cur_tx_id: str, win_tx_id: str, win_tx: str, status):
    """재서명 전의 옛 tx 가 블록에 들어갔으면 claim 을 그 tx 로 확정한다. **nonce 는 SET 하지 않는다.**

    상태 조건에 CREATED 도 넣는다 — 재서명 직후 stage 전(CREATED)에 옛 tx 가 이긴 경우도 확정한다."""
    return (
        update(Claim)
        .where(
            Claim.id == claim_id,
            Claim.tx_id == cur_tx_id,
            Claim.tx_status.in_((TxStatus.CREATED, TxStatus.STAGED, TxStatus.INVALID)),
        )
        .values(tx_id=win_tx_id, tx=win_tx, tx_status=status)
    )
