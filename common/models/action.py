from sqlalchemy import Column, BigInteger, Text, Integer, ForeignKey, Index
from sqlalchemy.orm import Mapped, relationship

from common.models.base import Base, TimeStampMixin, AutoIdMixin
from common.models.season_pass import SeasonPass


class Block(TimeStampMixin, Base):
    __tablename__ = "block"
    index = Column(BigInteger, primary_key=True, index=True, nullable=False)


class ActionHistory(AutoIdMixin, TimeStampMixin, Base):
    __tablename__ = "action_history"
    season_id = Column(Integer, ForeignKey("season_pass.id"), nullable=False)
    season: Mapped["SeasonPass"] = relationship("SeasonPass", foreign_keys=[season_id])
    agent_addr = Column(Text, nullable=False)
    avatar_addr = Column(Text, nullable=False)
    action = Column(Text, nullable=False, index=True)
    count = Column(Integer, nullable=False)
    exp = Column(Integer, nullable=False)

    __table_args__ = (
        Index("idx_season_avatar", "season_id", "avatar_addr"),
    )
