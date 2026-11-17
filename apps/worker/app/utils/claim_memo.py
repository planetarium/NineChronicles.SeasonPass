import json
from collections import Counter
from typing import List


def compact_levels(levels: List[int]) -> dict:
    """레벨 목록을 {"l": 중복 없는 레벨, "r": {반복된 레벨: 횟수}} 로 줄인다.

    최대 레벨을 넘긴 경험치는 `max_level + 1` 반복 보상으로 목록에 그대로 쌓인다
    (UserSeasonPass.available_rewards). 2026-11-14 인터널에서 반복이 40만 번 붙어 tx 가
    1.6MB 가 됐고, 블록 tx 크기 한도(1MB)를 넘어 영원히 포함되지 못한 채 그 nonce 뒤의
    지급이 전부 멈췄다. 메모에는 횟수만 남긴다.
    """
    counts = Counter(levels)
    return {
        "l": sorted(counts),
        "r": {str(lvl): c for lvl, c in sorted(counts.items()) if c > 1},
    }


def build_claim_memo(
    normal_levels: List[int], premium_levels: List[int], pass_type: str
) -> str:
    # 클라이언트는 메모에서 "season_pass" 와 "t" 값만 문자열로 찾는다(MailExtensions.cs).
    return json.dumps(
        {
            "season_pass": {
                "n": compact_levels(normal_levels),
                "p": compact_levels(premium_levels),
                "t": "claim",
                "tp": pass_type,
            }
        }
    )
