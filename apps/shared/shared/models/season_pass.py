from sqlalchemy import JSON, Column, DateTime, Enum, ForeignKey, Index, Integer
from sqlalchemy.dialects.postgresql import ENUM
from sqlalchemy.orm import relationship

from shared.enums import ActionType, PassType
from shared.models.base import AutoIdMixin, Base, TimeStampMixin


class SeasonPass(TimeStampMixin, Base):
    __tablename__ = "season_pass"
    id = Column(Integer, primary_key=True, index=True, nullable=False)
    pass_type = Column(
        Enum(PassType),
        nullable=False,
        server_default=PassType.COURAGE_PASS.name,
        doc="Type of this pass",
    )
    season_index = Column(
        Integer, nullable=False, doc="Season index of this type of pass."
    )
    start_timestamp = Column(
        DateTime(timezone=True),
        nullable=True,
        doc="Start datetime of this season. Inclusive.",
    )
    end_timestamp = Column(
        DateTime(timezone=True),
        nullable=True,
        doc="End datetime of this season. Inclusive.",
    )
    reward_list = Column(JSON, nullable=False, default=[])
    instant_exp = Column(
        Integer, nullable=False, doc="Instant reward exp for premium plus user"
    )

    exp_list = relationship("Exp")

    __table_args__ = (
        Index("ix_pass_type_index_unique", "pass_type", "season_index", unique=True),
    )

    @property
    def exp_dict(self):
        return {x.action_type: x.exp for x in self.exp_list}

    @property
    def start_date(self):
        return self.start_timestamp.date() if self.start_timestamp else None

    @property
    def end_date(self):
        return self.end_timestamp.date() if self.end_timestamp else None


class Level(AutoIdMixin, TimeStampMixin, Base):
    __tablename__ = "level"
    pass_type = Column(Enum(PassType), nullable=False)
    level = Column(Integer, nullable=False)
    exp = Column(Integer, nullable=False)


class Exp(AutoIdMixin, TimeStampMixin, Base):
    __tablename__ = "exp"
    season_pass_id = Column(Integer, ForeignKey("season_pass.id"), nullable=False)
    season_pass = relationship(
        "SeasonPass", foreign_keys=[season_pass_id], back_populates="exp_list"
    )
    action_type = Column(ENUM(ActionType), nullable=False)
    exp = Column(Integer, nullable=False)
