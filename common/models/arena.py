from sqlalchemy import Column, Integer, PrimaryKeyConstraint, LargeBinary

from common.models.base import Base


class BattleHistory(Base):
    __tablename__ = "battle_history"
    planet_id = Column(LargeBinary(length=12), nullable=False, doc="An identifier to distinguish network & planet")
    battle_id = Column(Integer, nullable=False)

    __table_args__ = (
        PrimaryKeyConstraint("planet_id", "battle_id", name="pk_battle_history"),
    )
