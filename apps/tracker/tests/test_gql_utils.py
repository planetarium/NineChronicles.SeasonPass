"""Address helpers used to derive per-avatar state addresses."""
import hashlib
import hmac

import eth_utils
import pytest
from app.utils.gql import checksum_encode, derive_address


@pytest.mark.parametrize(
    "addr",
    [
        "5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed",  # EIP-55 test vectors
        "fB6916095ca1df60bB79Ce92cE3Ea74c37c5d359",
        "dbF03B407c01E7cD3CBea99509d93f8DDDC8C6FB",
        "D1220A0cf47c7B9Be7A2E6BA89F429762e7b9aDb",
    ],
)
def test_checksum_encode_eip55(addr):
    assert checksum_encode(bytes.fromhex(addr)) == addr


@pytest.mark.parametrize("prefix", ["0x", ""])
def test_derive_address_matches_hmac_sha1(prefix):
    avatar = "7355c6eb16b02de1dae910cafed9ad8cbb19ebf8"
    expected = eth_utils.to_checksum_address(
        hmac.new(b"inventory", bytes.fromhex(avatar), hashlib.sha1).digest()
    )[2:]
    assert derive_address(prefix + avatar, "inventory") == expected
