"""claim 지급 tx 만들기. consumer(첫 서명)와 재서명(stage_task)이 같은 함수를 쓴다.

DB 를 만지지 않는 순수 함수다(서명만 KMS 를 부른다).
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.utils.claim_memo import build_claim_memo
from shared.utils.transaction import (
    create_grant_items_unsigned_tx,
    create_signed_tx,
    tx_timestamp,
)


@dataclass(frozen=True)
class ClaimTxFields:
    """tx 에 들어가는 claim 의 값. ORM 객체를 세션 밖으로 들고 다니지 않으려고 값만 뽑는다."""

    planet_id: bytes
    avatar_addr: str
    reward_list: List[Dict[str, Any]]
    normal_levels: List[int]
    premium_levels: List[int]
    pass_type: str  # PassType.value

    @classmethod
    def from_claim(cls, claim) -> "ClaimTxFields":
        return cls(
            planet_id=claim.planet_id,
            avatar_addr=claim.avatar_addr,
            reward_list=claim.reward_list,
            normal_levels=claim.normal_levels,
            premium_levels=claim.premium_levels,
            pass_type=claim.season_pass.pass_type.value,
        )


def build_unsigned_claim_tx(
    fields: ClaimTxFields, nonce: int, pubkey: bytes, address: str, timestamp: datetime
) -> bytes:
    """서명 전 tx. 재서명 전 크기 확인에도 쓴다(KMS 를 부르지 않는다).

    ⚠️ timestamp 는 클레임 생성 시각이 아니라 **지금**이어야 한다. 이 값은 tx 의 t 필드로
      들어가고 체인은 너무 과거인 tx 를 스테이징에서 거부한다. 클레임이 만들어진
      직후에 처리되면 둘이 같아서 티가 안 나지만, 재시도가 늦어지면 태어날 때부터
      만료된 tx 가 된다 — 그리고 그 tx 가 nonce 를 선점하므로 **뒤따르는 클레임이
      전부 막힌다**(구멍 뒤의 tx 는 스테이징에서 밀려나 차례로 INVALID 가 된다).

      2026-09-29 Heimdall 지급 정지가 이것이었다. beat 데드락으로 2.7일 묶여 있던
      클레임이 풀려나면서 2.7일 전 타임스탬프를 단 tx 를 만들었고, 그게 거부되며
      nonce 1794589 에 구멍이 생겨 뒤의 클레임이 전부 섰다.

      평소에 이게 안 터진 건 transaction.py 의 strftime 이 tzinfo 를 버리고 'Z' 를
      붙여서다 — created_at 이 KST(+09)로 와서 모든 tx 가 실제보다 9시간 미래로
      찍혔고, 그 우연한 여유분이 유일한 완충이었다. 즉 생성 후 9시간이 지나서
      처리되는 클레임은 원래부터 전부 만료된 tx 가 됐다.

      now(tz=utc) 를 쓰면 strftime 결과도 진짜 UTC 가 되어 두 문제가 같이 없어진다.
      같은 저장소의 burn_asset_task 가 이미 이렇게 한다.
    """
    memo = build_claim_memo(
        fields.normal_levels, fields.premium_levels, fields.pass_type
    )
    return create_grant_items_unsigned_tx(
        planet_id=fields.planet_id,
        public_key=pubkey.hex(),
        address=address,
        nonce=nonce,
        avatar_addr=fields.avatar_addr,
        claim_data=fields.reward_list,
        memo=memo,
        timestamp=timestamp,
    )


def build_signed_claim_tx(
    fields: ClaimTxFields, nonce: int, account, timestamp: datetime
) -> bytes:
    """서명한 tx. account 는 app.utils.aws.Account(서명은 KMS)."""
    unsigned_tx = build_unsigned_claim_tx(
        fields, nonce, account.pubkey, account.address, timestamp
    )
    signature = account.sign_tx(unsigned_tx)
    return create_signed_tx(unsigned_tx, signature)


def tx_signed_at(tx_hex: Optional[str]) -> Optional[datetime]:
    """tx 의 서명 시각(t, UTC). 읽지 못하면 None — 호출부는 None 을 "만료 아님"으로 본다."""
    return tx_timestamp(tx_hex)
