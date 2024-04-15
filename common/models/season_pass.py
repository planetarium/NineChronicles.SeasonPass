import datetime

from sqlalchemy import Column, Integer, JSON, ForeignKey, DateTime
from sqlalchemy.dialects.postgresql import ENUM
from sqlalchemy.orm import relationship

from common.enums import ActionType
from common.models.base import Base, TimeStampMixin, AutoIdMixin


class SeasonPass(TimeStampMixin, Base):
    __tablename__ = "season_pass"
    id = Column(Integer, primary_key=True, index=True, nullable=False)
    start_timestamp = Column(DateTime, nullable=False, doc="Start datetime of this season. Inclusive.")
    end_timestamp = Column(DateTime, nullable=False, doc="End datetime of this season. Inclusive.")
    reward_list = Column(JSON, nullable=False, default=[])
    instant_exp = Column(Integer, nullable=False, doc="Instant reward exp for premium plus user")

    exp_list = relationship("Exp")

    @property
    def exp_dict(self):
        return {x.action_type: x.exp for x in self.exp_list}

    @property
    def start_date(self):
        return self.start_timestamp.date()

    @property
    def end_date(self):
        return self.end_timestamp.date()

    @property
    def claim_limit_timestamp(self):
        return self.end_timestamp + datetime.timedelta(days=7)


class Level(AutoIdMixin, TimeStampMixin, Base):
    __tablename__ = "level"
    level = Column(Integer, nullable=False)
    exp = Column(Integer, nullable=False)


class Exp(AutoIdMixin, TimeStampMixin, Base):
    __tablename__ = "exp"
    season_pass_id = Column(Integer, ForeignKey("season_pass.id"), nullable=False)
    season_pass = relationship("SeasonPass", foreign_keys=[season_pass_id], back_populates="exp_list")
    action_type = Column(ENUM(ActionType), nullable=False)
    exp = Column(Integer, nullable=False)
