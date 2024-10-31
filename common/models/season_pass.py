from sqlalchemy import Column, Integer, JSON, ForeignKey, DateTime, Enum, Index
from sqlalchemy.dialects.postgresql import ENUM
from sqlalchemy.orm import relationship

from common.enums import ActionType, PassType
from common.models.base import Base, TimeStampMixin, AutoIdMixin


class SeasonPass(TimeStampMixin, Base):
    __tablename__ = "season_pass"
    id = Column(Integer, primary_key=True, index=True, nullable=False)
    pass_type = Column(Enum(PassType), nullable=False, server_default=PassType.COURAGE_PASS.name,
                       doc="Type of this pass")
    season_index = Column(Integer, nullable=False, doc="Season index of this type of pass.")
    start_timestamp = Column(DateTime(timezone=True), nullable=True, doc="Start datetime of this season. Inclusive.")
    end_timestamp = Column(DateTime(timezone=True), nullable=True, doc="End datetime of this season. Inclusive.")
    reward_list = Column(JSON, nullable=False, default=[])
    instant_exp = Column(Integer, nullable=False, doc="Instant reward exp for premium plus user")

    exp_list = relationship("Exp")

    __table_args__ = (
        Index("ix_pass_type_index_unique", "pass_type", "season_index", unique=True),
    )

    @property
    def exp_dict(self):
        return {x.action_type: x.exp for x in self.exp_list}

    @property
    def start_date(self):
        return self.start_timestamp.date()

    @property
    def end_date(self):
        return self.end_timestamp.date()


class Level(AutoIdMixin, TimeStampMixin, Base):
    __tablename__ = "level"
    pass_type = Column(Enum(PassType), nullable=False)
    level = Column(Integer, nullable=False)
    exp = Column(Integer, nullable=False)


class Exp(AutoIdMixin, TimeStampMixin, Base):
    __tablename__ = "exp"
    # TODO: 시즌 별로 Exp 가 달라질 가능성이 있나? 그리고 그 경우 기록이 필요한가? 질문하기
    #  필요하다면 이거 season_pass_id 를 살려야 한다.
    pass_type = Column(Enum(PassType), nullable=False)
    action_type = Column(ENUM(ActionType), nullable=False)
    exp = Column(Integer, nullable=False)
