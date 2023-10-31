from sqlalchemy import Column, Integer, Date, JSON, ForeignKey
from sqlalchemy.dialects.postgresql import ENUM
from sqlalchemy.orm import relationship

from common.enums import ActionType
from common.models.base import Base, TimeStampMixin, AutoIdMixin


class SeasonPass(TimeStampMixin, Base):
    __tablename__ = "season_pass"
    id = Column(Integer, primary_key=True, index=True, nullable=False)
    start_date = Column(Date, nullable=False, doc="Start date of pass. Inclusive.")
    end_date = Column(Date, nullable=False, doc="End date of pass. Inclusive.")
    reward_list = Column(JSON, nullable=False, default=[])

    exp_list = relationship("Exp")

    @property
    def exp_dict(self):
        return {x.action_type: x.exp for x in self.exp_list}


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
