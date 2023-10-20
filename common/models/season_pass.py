from sqlalchemy import Column, Integer, Date, JSON

from common.models.base import Base, TimeStampMixin, AutoIdMixin


class SeasonPass(TimeStampMixin, Base):
    __tablename__ = "season_pass"
    id = Column(Integer, primary_key=True, index=True, nullable=False)
    start_date = Column(Date, nullable=False, doc="Start date of pass. Inclusive.")
    end_date = Column(Date, nullable=False, doc="End date of pass. Inclusive.")
    reward_list = Column(JSON, nullable=False, default=[])


class Level(AutoIdMixin, TimeStampMixin, Base):
    __tablename__ = "level"
    level = Column(Integer, nullable=False)
    exp = Column(Integer, nullable=False)
