from sqlalchemy import Text, Column, Integer, ForeignKey, Boolean, Index
from sqlalchemy.orm import relationship, backref

from common.models.base import Base, TimeStampMixin, AutoIdMixin


class UserSeasonPass(AutoIdMixin, TimeStampMixin, Base):
    __tablename__ = "user_season_pass"
    agent_addr = Column(Text, nullable=False, index=True)
    avatar_addr = Column(Text, nullable=False, index=True)
    season_pass_id = Column(Integer, ForeignKey("season_pass.id"), nullable=False)
    season_pass = relationship("SeasonPass", foreign_keys=[season_pass_id], backref=backref("user_list"))
    is_premium = Column(Boolean, nullable=False, default=False)
    exp = Column(Integer, nullable=False, default=0)
    level = Column(Integer, nullable=False, default=0)
    last_normal_claim = Column(Integer, nullable=False, default=0, doc="Last claim order of normal reward")
    last_premium_claim = Column(Integer, nullable=False, default=0,
                                doc="Last claim order of premium reward. This only activated when is_premium == True")

    __table_args__ = (
        Index("avatar_season", "avatar_addr", "season_pass_id"),
    )
