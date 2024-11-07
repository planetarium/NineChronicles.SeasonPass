from enum import Enum


class PassType(Enum):
    """
    # Type of pass
    ---

    - **`CouragePass`** : [Seasonal] Former season pass. Explore stage and get brave exp.

    - **`AdventureBossPass`** : [Seasonal] Adventure boss pass. Explore adventure boss and get exp.

    - **`WorldClearPass`** : [One time] Clear world
    """

    COURAGE_PASS = "CouragePass"
    ADVENTURE_BOSS_PASS = "AdventureBossPass"
    WORLD_CLEAR_PASS = "WorldClearPass"


class TxStatus(Enum):
    """
    # Transaction Status
    ---
    Transaction status from IAP service to buyer to send purchased items.

    - **`Created`**

        The transaction is created, successfully signed and ready to stage.

    - **`Staged`**

        The transaction is successfully stated into the chain.

    - **`Success`**

        The transaction is successfully added to block.

    - **`Failure`**

        The transaction is failed.

    - **`Invalid`**

        The transaction is invalid.
        If you see this status, please contact to administrator.

    - **`Not Found`**

        The transaction is not found in chain.

    - **`Fail to Create`**

        Transaction creation is failed.
        If you see this status, please contact to administrator.

    - **`Unknown`**

        An unhandled error case. This is reserve to catch all other errors.
        If you see this status, please contact with administrator.

    """
    CREATED = "Created"
    STAGED = "Staged"
    SUCCESS = "Success"
    FAILURE = "Failure"
    INVALID = "Invalid"
    NOT_FOUND = "Not Found"
    FAIL_TO_CREATE = "Fail to Create"
    UNKNOWN = "Unknown"


class ActionType(Enum):
    """
    # ActionType
    ---
    Action types to give brave exp.

    - **`hack_and_slash`**: Stage adventure. Matches with type_id `hack_and_slash##`
    - **`hack_and_slash_sweep`**: Sweep stage. Matches with type_id `hack_and_slash_sweep##`
    - **`battle_arena`**: Arena battle. Matches with type_id `battle_arena##`
    - **`raid`**: World boss battle. Matches with type_id `raid##`
    - **`event_dungeon`**: Event dungeon stage. Matches with type_id `event_dungeon_battle##`
    - **`wanted`**: Add bounty to adventure boss
    - **`explore_adventure_boss`**: Explore adventure boss stage
    - **`sweep_adventure_boss`**: Rush adventure boss stage
    """
    HAS = "hack_and_slash"
    SWEEP = "hack_and_slash_sweep"
    ARENA = "battle_arena"
    RAID = "raid"
    EVENT = "event_dungeon"
    WANTED = "wanted"
    CHALLENGE = "explore_adventure_boss"
    RUSH = "sweep_adventure_boss"


class PlanetID(bytes, Enum):
    """
    # PlanetID
    ---
    Network & Planet recognizing UID

    - *0x000000000000* : Mainnet + Odin
    - *0x000000000001* : Mainnet + Heimdall
    - *0x000000000002* : Mainnet + Idun
    - *0x100000000000* : Internal + Odin
    - *0x100000000001* : Internal + Heimdall
    - *0x100000000002* : Internal + Idun

    """
    ODIN = b'0x000000000000'
    HEIMDALL = b'0x000000000001'
    IDUN = b'0x000000000002'

    ODIN_INTERNAL = b'0x100000000000'
    HEIMDALL_INTERNAL = b'0x100000000001'
    IDUN_INTERNAL = b'0x100000000002'
