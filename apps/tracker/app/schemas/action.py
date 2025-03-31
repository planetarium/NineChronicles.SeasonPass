from typing import List, Optional

from pydantic.v1.dataclasses import dataclass

AP_PER_STONE = 120


@dataclass
class AdventureBossActionJson:
    id: str
    type_id: str

    # wanted
    a: Optional[str] = None  # AvatarAddress
    b: Optional[list] = None  # Bounty
    s: Optional[str] = None  # Season

    # challenge
    avatarAddress: Optional[str] = None
    costumes: Optional[list] = None
    equipments: Optional[list] = None
    foods: Optional[list] = None
    r: Optional[list] = None  # Rune
    season: Optional[str] = None

    # rush
    # a: str  # AvatarAddress
    c: Optional[list] = None  # Costume
    e: Optional[list] = None  # Equipments

    # r: list  # Rune
    # s: str

    @property
    def avatar_addr(self):
        return self.a or self.avatarAddress

    @property
    def season_index(self) -> int:
        return int(self.s or self.season)

    @property
    def count_base(self):
        if self.type_id.startswith("wanted"):
            return int(int(self.b[-1]) / 100)
        else:
            # AP potion usage is defined inside action. Handler should get this.
            return 0


@dataclass
class ActionJson:
    id: str
    type_id: str

    # hack_and_slash22
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

    # battle
    maa: Optional[str] = None
    arp: Optional[str] = None
    m: Optional[str] = None

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
            or self.maa  # Battle
            or self.a  # Raid
            or self.l[0]  # Event dungeon
        )

    @property
    def count_base(self) -> int:
        if self.type_id.startswith("raid"):
            return 1
        elif self.type_id.startswith("battle"):
            return 1
        elif "sweep" in self.type_id:
            # !!! WARNING: This returns used AP, not play count !!!
            return self.actionPoint + self.apStoneCount * AP_PER_STONE
        elif "event" in self.type_id:
            return 1
        else:  # hack_and_slash
            return (
                self.totalPlayCount
            )  # This includes AP and AP Potion usage + Staking modification.
