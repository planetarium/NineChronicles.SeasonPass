import datetime
from typing import Any, Dict, List

import bencodex
from shared.enums import PlanetID
from shared.utils.actions import Address, BurnAsset, ClaimItems, FungibleAssetValue


def create_unsigned_tx(
    planet_id: PlanetID,
    public_key: str,
    address: str,
    nonce: int,
    plain_value: Dict[str, Any],
    timestamp: datetime.datetime,
) -> bytes:
    if address.startswith("0x"):
        address = address[2:]
    return bencodex.dumps(
        {
            # Raw action value
            b"a": [plain_value],
            # Genesis block hash
            b"g": get_genesis_block_hash(planet_id),
            # GasLimit (see also GasLimit list section below)
            b"l": 4,
            # MaxGasPrice (see also Mead section for the currency spec)
            b"m": [
                {"decimalPlaces": b"\x12", "minters": None, "ticker": "Mead"},
                10_000_000_000_000,
            ],
            # Nonce
            b"n": nonce,
            # Public key
            b"p": bytes.fromhex(public_key),
            # Signer
            b"s": bytes.fromhex(address),
            # Timestamp
            b"t": timestamp.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            # Updated addresses
            b"u": [],
        }
    )


def append_signature_to_unsigned_tx(unsigned_tx: bytes, signature: bytes) -> bytes:
    decoded = bencodex.loads(unsigned_tx)
    decoded[b"S"] = signature
    return bencodex.dumps(decoded)


def get_genesis_block_hash(planet_id: PlanetID) -> bytes:
    switcher = {
        PlanetID.ODIN: bytes.fromhex(
            "4582250d0da33b06779a8475d283d5dd210c683b9b999d74d03fac4f58fa6bce"
        ),
        PlanetID.ODIN_INTERNAL: bytes.fromhex(
            "4582250d0da33b06779a8475d283d5dd210c683b9b999d74d03fac4f58fa6bce"
        ),
        PlanetID.HEIMDALL: bytes.fromhex(
            "729fa26958648a35b53e8e3905d11ec53b1b4929bf5f499884aed7df616f5913"
        ),
        PlanetID.HEIMDALL_INTERNAL: bytes.fromhex(
            "729fa26958648a35b53e8e3905d11ec53b1b4929bf5f499884aed7df616f5913"
        ),
    }
    if planet_id not in switcher:
        raise ValueError("Invalid planet id")
    return switcher[planet_id]


def create_claim_items_unsigned_tx(
    planet_id: PlanetID,
    public_key: str,
    address: str,
    nonce: int,
    avatar_addr: str,
    claim_data: List[Dict[str, Any]],
    memo: str,
    timestamp: datetime.datetime,
) -> bytes:
    """
    claim_items 액션을 위한 unsigned transaction을 생성합니다.
    GQL 의존성을 제거하고 로컬에서 생성합니다.
    """
    if not claim_data:
        raise ValueError("Nothing to claim")

    # claim_data를 ClaimItems 형식으로 변환
    claim_items_data = []
    for item in claim_data:
        # FungibleAssetValue 객체 생성
        fungible_asset_value = FungibleAssetValue.from_raw_data(
            ticker=item["ticker"],
            decimal_places=item.get("decimal_places", 0),
            minters=None,
            amount=item["amount"],
        )

        claim_items_data.append(
            {
                "avatarAddress": Address(avatar_addr),  # Address 객체로 변환
                "fungibleAssetValues": [
                    fungible_asset_value
                ],  # FungibleAssetValue 객체로 변환
            }
        )

    # ClaimItems 객체 생성
    claim_items = ClaimItems(
        claim_data=claim_items_data,
        memo=memo,
        _id="ae6d745a6dc911f0a9005210846b4679",  # 고정된 ID 사용
    )

    # plain_value 생성
    plain_value = claim_items.plain_value

    # unsigned transaction 생성
    return create_unsigned_tx(
        planet_id=planet_id,
        public_key=public_key,
        address=address,
        nonce=nonce,
        plain_value=plain_value,
        timestamp=timestamp,
    )


def create_burn_asset_unsigned_tx(
    planet_id: PlanetID,
    public_key: str,
    address: str,
    nonce: int,
    owner: str,
    ticker: str,
    decimal_places: int,
    amount: str,
    memo: str,
    timestamp: datetime.datetime,
) -> bytes:
    """
    burn_asset 액션을 위한 unsigned transaction을 생성합니다.
    GQL 의존성을 제거하고 로컬에서 생성합니다.
    """
    # FungibleAssetValue 객체 생성
    from decimal import Decimal

    fungible_asset_value = FungibleAssetValue.from_raw_data(
        ticker=ticker,
        decimal_places=decimal_places,
        minters=None,
        amount=Decimal(amount),
    )

    # BurnAsset 객체 생성
    burn_asset = BurnAsset(
        owner=Address(owner),
        amount=fungible_asset_value,
        memo=memo,
        _id="burn_asset_tx_id",  # 고정된 ID 사용
    )

    # plain_value 생성
    plain_value = burn_asset.plain_value

    # unsigned transaction 생성
    return create_unsigned_tx(
        planet_id=planet_id,
        public_key=public_key,
        address=address,
        nonce=nonce,
        plain_value=plain_value,
        timestamp=timestamp,
    )


def create_signed_tx(unsigned_tx: bytes, signature: bytes) -> bytes:
    """
    unsigned transaction에 서명을 추가하여 signed transaction을 생성합니다.
    GQL 의존성을 제거하고 로컬에서 생성합니다.
    """
    return append_signature_to_unsigned_tx(unsigned_tx, signature)
