"""track_tx — one planet's lookup failure must not block the other planets.

A single exception from one headless lookup (timeout, TransportQueryError) used
to end the whole cycle, so no status got written for any planet. The read
window was one 200-row slice ordered by id, so a stalled planet's old claims
could fill it and starve the others.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from app.trackers import tx_tracker
from shared.enums import PlanetID, TxStatus
from sqlalchemy.dialects import postgresql

ODIN = PlanetID.ODIN
HEIMDALL = PlanetID.HEIMDALL
URL_MAP = {
    ODIN.value.decode(): "http://odin",
    HEIMDALL.value.decode(): "http://heimdall",
}


def claim(planet_id, claim_id, tx_id, tx_status=TxStatus.STAGED):
    return SimpleNamespace(
        id=claim_id,
        planet_id=planet_id.value,
        tx_id=tx_id,
        tx_status=tx_status,
        nonce=claim_id,
        tx=None,
    )


def params(stmt):
    return stmt.compile(dialect=postgresql.dialect()).params


@pytest.fixture
def run(monkeypatch):
    monkeypatch.setattr(tx_tracker.config, "gql_url_map", URL_MAP)

    def _run(rows_by_planet, lookup):
        read_sess, write_sess = MagicMock(), MagicMock()
        read_sess.scalars.side_effect = [
            MagicMock(fetchall=MagicMock(return_value=rows))
            for rows in rows_by_planet.values()
        ]
        with patch.object(
            tx_tracker, "scoped_session", side_effect=[read_sess, write_sess]
        ), patch.object(tx_tracker, "process", side_effect=lookup), patch.object(
            tx_tracker, "history_table_ready", return_value=False
        ), patch.object(
            tx_tracker, "logger"
        ) as logger:
            tx_tracker.track_tx()
        written = {
            params(c.args[0])["id_1"]: params(c.args[0])["tx_status"]
            for c in write_sess.execute.call_args_list
        }
        return written, read_sess, write_sess, logger

    return _run


def test_one_failed_lookup_does_not_block_others(run):
    def lookup(planet_id, tx_id):
        if planet_id == ODIN:
            raise TimeoutError("odin-rpc read timed out")
        return tx_id, TxStatus.SUCCESS, "null"

    written, _, write_sess, _ = run(
        {
            ODIN: [claim(ODIN, 1, "o1")],
            HEIMDALL: [claim(HEIMDALL, 2, "h1"), claim(HEIMDALL, 3, "h2")],
        },
        lookup,
    )

    # Heimdall results are written; the failed Odin claim is left untouched (not
    # NULL, not INVALID) so the next cycle asks again.
    assert written == {2: TxStatus.SUCCESS, 3: TxStatus.SUCCESS}
    write_sess.commit.assert_called_once()


def test_all_failed_writes_nothing(run):
    def lookup(planet_id, tx_id):
        raise ConnectionError("down")

    written, _, write_sess, _ = run(
        {ODIN: [claim(ODIN, 1, "o1")], HEIMDALL: []}, lookup
    )

    assert written == {}
    write_sess.execute.assert_not_called()


def test_failures_are_logged_per_planet(run):
    def lookup(planet_id, tx_id):
        if planet_id == ODIN:
            raise TimeoutError("odin-rpc read timed out")
        return tx_id, TxStatus.SUCCESS, "null"

    _, _, _, logger = run(
        {
            ODIN: [claim(ODIN, 1, "o1"), claim(ODIN, 2, "o2")],
            HEIMDALL: [claim(HEIMDALL, 3, "h1")],
        },
        lookup,
    )

    errors = [c for c in logger.error.call_args_list]
    assert len(errors) == 1
    assert errors[0].kwargs["planet_id"] == ODIN.value
    assert errors[0].kwargs["failed"] == 2
    assert errors[0].kwargs["errors"] == {"TimeoutError": 2}


def test_each_planet_is_queried_with_its_own_limit(run):
    _, read_sess, _, _ = run(
        {ODIN: [], HEIMDALL: []}, lambda p, t: (t, TxStatus.SUCCESS, "null")
    )

    queried = [params(c.args[0]) for c in read_sess.scalars.call_args_list]
    assert [q["planet_id_1"] for q in queried] == [ODIN.value, HEIMDALL.value]
    assert all(q["param_1"] == tx_tracker.PLANET_LIMIT for q in queried)


def test_stalled_planet_does_not_starve_others(run):
    # Odin fills its whole window; Heimdall's claims are still looked up.
    odin = [claim(ODIN, i, f"o{i}", TxStatus.INVALID) for i in range(100)]
    seen = []

    def lookup(planet_id, tx_id):
        seen.append(planet_id)
        return tx_id, TxStatus.INVALID, "null"

    run({ODIN: odin, HEIMDALL: [claim(HEIMDALL, 10_000, "h1")]}, lookup)

    assert seen.count(HEIMDALL) == 1


def test_summary_log_tells_unchanged_from_changed(run):
    def lookup(planet_id, tx_id):
        return tx_id, (TxStatus.INVALID if tx_id == "o1" else TxStatus.SUCCESS), "null"

    _, _, _, logger = run(
        {
            ODIN: [claim(ODIN, 1, "o1", TxStatus.INVALID)],
            HEIMDALL: [claim(HEIMDALL, 2, "h1", TxStatus.STAGED)],
        },
        lookup,
    )

    lines = [c.args[0] for c in logger.info.call_args_list if c.args]
    assert "1 transactions are still INVALID" in lines
    assert "1 transactions are changed to SUCCESS" in lines
