from datetime import timedelta

from shared.enums import TxStatus
from shared.models.base import AutoIdMixin, Base, TimeStampMixin
from shared.models.season_pass import SeasonPass
from shared.utils.season_pass import get_max_level
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, ENUM, JSONB
from sqlalchemy.orm import Mapped, backref, relationship


class UserSeasonPass(AutoIdMixin, TimeStampMixin, Base):
    __tablename__ = "user_season_pass"
    planet_id = Column(
        LargeBinary(length=12),
        nullable=False,
        doc="An identifier to distinguish network & planet",
    )
    agent_addr = Column(Text, nullable=False)
    avatar_addr = Column(Text, nullable=False)
    season_pass_id = Column(Integer, ForeignKey("season_pass.id"), nullable=False)
    season_pass: Mapped["SeasonPass"] = relationship(
        "SeasonPass", foreign_keys=[season_pass_id], backref=backref("user_list")
    )
    is_premium = Column(Boolean, nullable=False, default=False)
    is_premium_plus = Column(Boolean, nullable=False, default=False)
    exp = Column(
        Integer, nullable=False, default=0
    )  # This is used as last cleared stage ID for world clear pass
    level = Column(
        Integer, nullable=False, default=0
    )  # This is used as current world ID for world clear pass
    last_normal_claim = Column(
        Integer, nullable=False, default=0, doc="Last claim order of normal reward"
    )
    last_premium_claim = Column(
        Integer,
        nullable=False,
        default=0,
        doc="Last claim order of premium reward. This only activated when is_premium == True",
    )

    def available_rewards(self, sess):
        max_level, repeat_exp = get_max_level(sess, self.season_pass.pass_type)
        rewards = {
            "normal": []
            if self.level == self.last_normal_claim
            else list(
                range(
                    self.last_normal_claim + 1, min(self.level + 1, max_level.level + 1)
                )
            ),
            "premium": []
            if (not self.is_premium or self.level == self.last_premium_claim)
            else list(
                range(
                    self.last_premium_claim + 1,
                    min(self.level + 1, max_level.level + 1),
                )
            ),
        }
        # Get repeating reward
        # NOTE: In case of World clear pass, no one can exceed max level.
        if self.level > max_level.level:
            rewards["normal"].extend(
                [max_level.level + 1] * ((self.exp - max_level.exp) // repeat_exp)
            )

        return rewards

    @property
    def claim_limit_timestamp(self):
        return (
            (self.season_pass.end_timestamp + timedelta(days=7))
            if self.season_pass.end_timestamp
            else None
        )

    __table_args__ = (
        Index("avatar_season", "avatar_addr", "season_pass_id"),
        UniqueConstraint(
            "planet_id", "season_pass_id", "avatar_addr", name="user_season_pass_unique"
        ),
    )


class Claim(AutoIdMixin, TimeStampMixin, Base):
    __tablename__ = "claim"
    uuid = Column(Text, nullable=False, index=True)
    agent_addr = Column(Text, nullable=False)
    avatar_addr = Column(Text, nullable=False)
    # FIXME: season_pass_id of claim should be to exist.
    #  But we cannot automatically find prev. claims' season_pass_id in migration code.
    #  TODO: Set all prev. claims' season_pass_id and set it to not-null column.
    season_pass_id = Column(Integer, ForeignKey("season_pass.id"), nullable=True)
    season_pass: Mapped["SeasonPass"] = relationship(
        "SeasonPass", foreign_keys=[season_pass_id]
    )
    normal_levels = Column(ARRAY(Integer), nullable=False, default=[])
    premium_levels = Column(ARRAY(Integer), nullable=False, default=[])
    reward_list = Column(JSONB, nullable=False)
    planet_id = Column(
        LargeBinary(length=12),
        nullable=False,
        doc="An identifier to distinguish network & planet",
    )
    nonce = Column(Integer, nullable=True)
    tx = Column(Text, nullable=True)
    tx_id = Column(Text, nullable=True)
    tx_status = Column(ENUM(TxStatus), nullable=True)

    __table_args__ = (
        UniqueConstraint("planet_id", "nonce", name="claim_planet_nonce_unique"),
    )


class ClaimTxHistory(AutoIdMixin, TimeStampMixin, Base):
    """claim 에서 교체된 옛 tx. 추가만 한다(운영 절차의 reason·bound_nonce 갱신만 예외).

    같은 nonce 로 다시 서명(재서명)한 뒤에도 옛 tx 가 다른 노드에 남아 먼저 블록에 들어갈 수 있다.
    tx 추적기가 이 tx 들도 조회해, 지급된 tx 로 claim 을 확정한다.
    이 행(released 제외)이 있는 claim 은 consumer 가 새 nonce 를 배정하지 않는다 — 한 번 tx 가
    나간 claim 의 nonce 가 바뀌지 않아야 같은 지급이 두 번 들어가지 않는다(같은 서명자·같은 nonce 의
    tx 는 블록에 하나만 들어간다).

    claim 에 컬럼을 더하지 않고 따로 둔 이유: 마이그레이션보다 이미지가 먼저 뜨면 Claim 을 읽는 모든
    쿼리가 실패한다. 따로 두면 테이블이 없을 때 재서명·기록 확정만 꺼진다(claim_tx_history.py).
    """

    __tablename__ = "claim_tx_history"
    claim_id = Column(Integer, ForeignKey("claim.id"), nullable=False, index=True)
    planet_id = Column(LargeBinary(length=12), nullable=False)
    # 언제나 claim.nonce 와 같다(재서명은 nonce 를 바꾸지 않는다).
    nonce = Column(Integer, nullable=False)
    tx_id = Column(Text, nullable=False, unique=True)
    tx = Column(Text, nullable=False)
    # 옛 tx 의 t(서명 시각). 읽지 못했으면 NULL.
    signed_at = Column(DateTime(timezone=True), nullable=True)
    # 재서명 판단 때 검증자의 nextTxNonce(V). 구멍 주인(nonce == V)을 재서명했을 때만 적는다.
    # NULL 이면 재서명 상한 집계에서 빠진다(사람이 원인을 고친 뒤 상한을 풀 때도 NULL 로 바꾼다).
    bound_nonce = Column(Integer, nullable=True)
    # "expired": 재서명으로 교체된 옛 tx. 추적기가 이 tx 가 이겼는지 계속 확인한다.
    # "lost": 기록 tx 가 이겨서 밀려난 tx. 추적기는 확인하지 않는다.
    # "released": 증명을 마치고 사람이 nonce 를 풀어 준 claim 의 기록. 추적기·consumer 모두 보지 않는다.
    reason = Column(Text, nullable=False)
