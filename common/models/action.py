from sqlalchemy import Column, BigInteger, Text, Integer, ForeignKey, Index, LargeBinary, UniqueConstraint, Enum
from sqlalchemy.dialects.postgresql import ENUM
from sqlalchemy.orm import Mapped, relationship

from common.enums import ActionType, PassType
from common.models.base import Base, TimeStampMixin, AutoIdMixin
from common.models.season_pass import SeasonPass


class Block(AutoIdMixin, TimeStampMixin, Base):
    __tablename__ = "block"
    pass_type = Column(Enum(PassType), nullable=False)
    planet_id = Column(LargeBinary(length=12), nullable=False, doc="An identifier to distinguish network & planet")
    index = Column(BigInteger, nullable=False)

    __table_args__ = (
        Index("idx_block_planet_pass_type_index", "planet_id", "pass_type", "index"),
        UniqueConstraint("planet_id", "index", name="block_by_planet_unique"),
    )


class ActionHistory(AutoIdMixin, TimeStampMixin, Base):
    __tablename__ = "action_history"
    planet_id = Column(LargeBinary(length=12), nullable=False, doc="An identifier to distinguish network & planet")
    season_id = Column(Integer, ForeignKey("season_pass.id"), nullable=False)
    season: Mapped["SeasonPass"] = relationship("SeasonPass", foreign_keys=[season_id])
    block_index = Column(Integer, nullable=False)
    tx_id = Column(Text, nullable=False)
    agent_addr = Column(Text, nullable=False)
    avatar_addr = Column(Text, nullable=False)
    action = Column(ENUM(ActionType), nullable=False, index=True)
    count = Column(Integer, nullable=False)
    exp = Column(Integer, nullable=False)

    __table_args__ = (
        Index("idx_season_avatar", "season_id", "avatar_addr"),
    )


class AdventureBossHistory(AutoIdMixin, TimeStampMixin, Base):
    __tablename__ = "adventure_boss_history"
    planet_id = Column(LargeBinary(length=12), nullable=False, doc="An identifier to distinguish network & planet")
    agent_addr = Column(Text, nullable=False)
    avatar_addr = Column(Text, nullable=False)
    season = Column(Integer, nullable=False)
    floor = Column(Integer, nullable=False)

    __table_args__ = (
        Index("idx_adventure_boss_floor_history", "season", "planet_id", "avatar_addr"),
    )
