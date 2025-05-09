from datetime import timedelta

from sqlalchemy import (
    Boolean,
    Column,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, ENUM, JSONB
from sqlalchemy.orm import Mapped, backref, relationship

from shared.enums import TxStatus
from shared.models.base import AutoIdMixin, Base, TimeStampMixin
from shared.models.season_pass import SeasonPass
from shared.utils.season_pass import get_max_level


class UserSeasonPass(AutoIdMixin, TimeStampMixin, Base):
    __tablename__ = "user_season_pass"
    planet_id = Column(
        LargeBinary(length=12),
        nullable=False,
        doc="An identifier to distinguish network & planet",
    )
    agent_addr = Column(Text, nullable=False, index=True)
    avatar_addr = Column(Text, nullable=False, index=True)
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
                    self.last_premium_claim + 1, min(self.level + 1, max_level.level + 1)
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
