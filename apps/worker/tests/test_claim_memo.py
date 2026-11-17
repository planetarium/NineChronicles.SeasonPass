"""클레임 tx 메모는 반복 보상 횟수와 상관없이 작아야 한다.

2026-11-14 인터널: 최대 레벨(31) 위 반복 보상 40만 번이 메모에 그대로 들어가 tx 가 1.6MB 가 됐고,
블록 tx 크기 한도(1MB)를 넘어 nonce 1814700 에서 지급이 통째로 멈췄다.
"""
import json

from app.utils.claim_memo import build_claim_memo, compact_levels


def test_compact_levels_counts_repeats():
    assert compact_levels([29, 30, 31, 32, 32, 32]) == {
        "l": [29, 30, 31, 32],
        "r": {"32": 3},
    }


def test_compact_levels_without_repeats():
    assert compact_levels([1, 2]) == {"l": [1, 2], "r": {}}
    assert compact_levels([]) == {"l": [], "r": {}}


def test_memo_stays_small_for_huge_repeat():
    normal = list(range(1, 32)) + [32] * 400_000
    memo = build_claim_memo(normal, list(range(1, 32)), "CouragePass")

    assert len(memo) < 1_000
    body = json.loads(memo)["season_pass"]
    assert body["n"]["r"] == {"32": 400_000}
    assert body["p"]["l"] == list(range(1, 32))


def test_memo_keeps_markers_the_client_reads():
    # MailExtensions.cs 는 "season_pass" 포함 여부와 "t" 값을 문자열로 찾는다.
    memo = build_claim_memo([1], [], "CouragePass")

    assert "season_pass" in memo
    assert '"t": "claim"' in memo
