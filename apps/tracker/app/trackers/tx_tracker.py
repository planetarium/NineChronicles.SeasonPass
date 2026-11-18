import concurrent.futures
import json
from collections import Counter, defaultdict
from typing import Optional, Tuple

import structlog
from gql.dsl import DSLQuery, dsl_gql
from shared.enums import PlanetID, TxStatus
from shared.models.user import Claim
from shared.utils._graphql import GQLClient
from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import scoped_session, sessionmaker

from app.config import config

logger = structlog.get_logger(__name__)

# Picked per planet (at most planets x PLANET_LIMIT per cycle). A single
# 200-row window ordered by id let a stalled planet's old claims fill it, so the
# other planets' txs were never looked up until those cleared.
PLANET_LIMIT = 100

engine = create_engine(str(config.pg_dsn), pool_size=5, max_overflow=5)


def process(
    planet_id: PlanetID, tx_id: str
) -> Tuple[str, Optional[TxStatus], Optional[str]]:
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
    resp = client.execute(query)
    logger.debug(resp)

    if "errors" in resp:
        logger.error("GQL failed to get transaction status", errors=resp["errors"])
        return tx_id, TxStatus.INVALID, json.dumps(resp["errors"])

    try:
        return (
            tx_id,
            TxStatus[resp["transaction"]["transactionResult"]["txStatus"]],
            json.dumps(resp["transaction"]["transactionResult"]["exceptionNames"]),
        )
    except:
        return (
            tx_id,
            TxStatus.INVALID,
            json.dumps(resp["transaction"]["transactionResult"]["exceptionNames"]),
        )


def track_tx():
    logger.info("Tracking unfinished transactions")

    # Read the unsettled claims in a short transaction, then release the
    # connection *before* the per-tx headless calls below. Previously this one
    # session stayed open across the whole GQL batch, so when a node was slow it
    # sat idle-in-transaction for minutes (observed 448s). That pinned the xmin
    # horizon and blocked autovacuum on claim/user_season_pass (dead tuples piled
    # up). Capture just the scalars we need, then close the session so the RPC
    # batch runs untethered to any DB transaction.
    read_sess = scoped_session(sessionmaker(bind=engine))
    try:
        targets = []  # (claim_id, planet_id, tx_id, old_status)
        for planet_id in config.converted_gql_url_map:
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
                .order_by(Claim.id)
                .limit(PLANET_LIMIT)
            ).fetchall()
            targets.extend(
                (claim.id, planet_id, claim.tx_id, claim.tx_status)
                for claim in claim_list
            )
    finally:
        read_sess.close()

    if not targets:
        logger.info("No transactions to track", tracker="tx_tracker")
        return

    # Query headless for each tx with no DB transaction held open. Each result is
    # taken on its own: one tx's exception (node timeout, GQL error) used to end
    # the whole cycle, so no other planet's status got written either. A failed
    # lookup leaves the claim's status as it is (never NULL or INVALID) and is
    # asked again next cycle.
    result = defaultdict(list)  # (old_status, new_status) -> [tx_id]
    updates = []  # (claim_id, tx_id, new_status)
    failures = defaultdict(lambda: defaultdict(int))  # planet_id -> {exc name: count}
    failure_sample = {}  # planet_id -> first error message, for diagnosis
    futures = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        for claim_id, planet_id, tx_id, old_status in targets:
            futures[executor.submit(process, planet_id, tx_id)] = (
                claim_id,
                planet_id,
                old_status,
            )

        for future in concurrent.futures.as_completed(futures):
            claim_id, planet_id, old_status = futures[future]
            try:
                tx_id, tx_status, msg = future.result()
            except Exception as e:
                failures[planet_id][type(e).__name__] += 1
                failure_sample.setdefault(planet_id, str(e)[:200])
                continue
            result[(old_status, tx_status)].append(tx_id)
            updates.append((claim_id, tx_id, tx_status))
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
            for planet_id, count in Counter(t[1] for t in targets).items()
        },
    )

    # Persist the resolved statuses in a fresh, short write transaction. The
    # read->write gap is wider now that the GQL batch runs outside the
    # transaction, so guard the write: match on tx_id (skip a claim re-staged
    # with a new tx in between) AND require the row still be STAGED/INVALID (skip
    # one already finalized to SUCCESS/FAILURE by another path), so we never
    # clobber a settled status with a stale GQL result.
    write_sess = scoped_session(sessionmaker(bind=engine))
    try:
        # claim id 순으로 쓴다 — 배포 중 tx_tracker 둘이 겹쳐도 서로 다른 순서로 행을 잠가 교착하지 않게.
        for claim_id, tx_id, tx_status in sorted(updates, key=lambda u: u[0]):
            write_sess.execute(
                update(Claim)
                .where(
                    Claim.id == claim_id,
                    Claim.tx_id == tx_id,
                    Claim.tx_status.in_((TxStatus.STAGED, TxStatus.INVALID)),
                )
                .values(tx_status=tx_status)
            )
        write_sess.commit()
    finally:
        write_sess.close()

    # Keys used to be status-name strings compared against None/TxStatus, which
    # never matched, so unchanged claims were logged as "changed" every cycle.
    for (old_status, status), tx_list in result.items():
        if status == old_status:
            logger.info(f"{len(tx_list)} transactions are still {status.name}")
        else:
            logger.info(f"{len(tx_list)} transactions are changed to {status.name}")
