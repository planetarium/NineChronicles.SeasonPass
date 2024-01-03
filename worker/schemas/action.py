from typing import Optional, List

from pydantic.v1.dataclasses import dataclass

AP_PER_STONE = 120


@dataclass
class ActionJson:
    id: str
    type_id: str

    # hack_and_slash21
    avatarAddress: Optional[str] = None
    apStoneCount: Optional[int] = None
    costumes: Optional[List[str]] = None
    equipments: Optional[List[str]] = None
    foods: Optional[List[str]] = None
    r: Optional[List[List[int]]] = None
    stageId: Optional[int] = None
    totalPlayCount: Optional[int] = None
    worldId: Optional[int] = None

    # hack_and_slash_sweep9
    # avatarAddress: Optional[str] = None
    actionPoint: Optional[int] = None
    # apStoneCount: Optional[int] = None
    # costumes: Optional[List[str]] = None
    # equipments: Optional[List[str]] = None
    runeInfos: Optional[List[List[int]]] = None
    # stageId: Optional[int] = None
    # worldId: Optional[int] = None

    # battle_arena13
    chi: Optional[int] = None
    cs: Optional[List[str]] = None
    eaa: Optional[str] = None
    es: Optional[List[str]] = None
    maa: Optional[str] = None
    rd: Optional[int] = None
    ri: Optional[List[List[int]]] = None
    tk: Optional[int] = None

    # raid6
    a: Optional[str] = None
    c: Optional[List[str]] = None
    e: Optional[List[str]] = None
    f: Optional[List[str]] = None
    p: Optional[bool] = None
    # r: Optional[List[List[int]]] = None

    # event_dungeon_battle6
    l: List = None

    @property
    def avatar_addr(self) -> str:
        return (
                self.avatarAddress  # HAS / Sweep
                or self.maa  # Arena
                or self.a  # Raid
                or self.l[0]  # Event dungeon
        )

    @property
    def count_base(self) -> int:
        if self.type_id.startswith("raid"):
            return 1
        elif self.type_id.startswith("battle_arena"):
            return self.tk
        elif "sweep" in self.type_id:
            # !!! WARNING: This returns used AP, not play count !!!
            return self.actionPoint + self.apStoneCount * AP_PER_STONE
        elif "event" in self.type_id:
            return 1
        else:  # hack_and_slash
            return self.totalPlayCount  # This includes AP and AP Potion usage + Staking modification.
