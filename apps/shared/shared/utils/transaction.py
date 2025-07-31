import datetime
from typing import Any, Dict

import bencodex
from shared.enums import PlanetID


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
