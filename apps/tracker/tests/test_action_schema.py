"""Action payload → (avatar, count_base): what every pass's exp is multiplied by."""
import pytest
from app.schemas.action import AP_PER_STONE, ActionJson, AdventureBossActionJson

AVATAR = "0x" + "ab" * 20


@pytest.mark.parametrize(
    "fields,avatar",
    [
        ({"type_id": "hack_and_slash22", "avatarAddress": AVATAR}, AVATAR),
        ({"type_id": "battle", "maa": AVATAR}, AVATAR),
        ({"type_id": "raid7", "a": AVATAR}, AVATAR),
        ({"type_id": "event_dungeon_battle6", "l": [AVATAR, "x"]}, AVATAR),
    ],
)
def test_avatar_addr_per_action(fields, avatar):
    assert ActionJson(id="1", **fields).avatar_addr == avatar


def test_count_base_hack_and_slash_is_total_play_count():
    a = ActionJson(
        id="1",
        type_id="hack_and_slash22",
        avatarAddress=AVATAR,
        totalPlayCount=7,
        apStoneCount=2,
    )
    assert a.count_base == 7


def test_count_base_sweep_is_used_ap_including_stones():
    a = ActionJson(
        id="1",
        type_id="hack_and_slash_sweep10",
        avatarAddress=AVATAR,
        actionPoint=30,
        apStoneCount=2,
    )
    assert a.count_base == 30 + 2 * AP_PER_STONE


@pytest.mark.parametrize(
    "type_id,fields",
    [
        ("raid7", {"a": AVATAR}),
        ("battle", {"maa": AVATAR}),
        ("event_dungeon_battle6", {"l": [AVATAR]}),
    ],
)
def test_count_base_one_per_raid_battle_event(type_id, fields):
    assert ActionJson(id="1", type_id=type_id, **fields).count_base == 1


def test_adventure_boss_wanted():
    a = AdventureBossActionJson(
        id="1", type_id="wanted", a=AVATAR, b=["NCG", "250"], s="7"
    )
    assert (a.avatar_addr, a.season_index, a.count_base) == (
        AVATAR,
        7,
        2,
    )  # bounty / 100, floored


@pytest.mark.parametrize("type_id", ["explore_adventure_boss", "sweep_adventure_boss"])
def test_adventure_boss_challenge_and_rush(type_id):
    a = AdventureBossActionJson(
        id="1", type_id=type_id, avatarAddress=AVATAR, season="3"
    )
    assert (a.avatar_addr, a.season_index, a.count_base) == (
        AVATAR,
        3,
        0,
    )  # floors come from the action result
