import hmac
from hashlib import sha1
from typing import Union

import eth_utils
from pyasn1.type import namedtype, univ


class ECDSASignatureRecord(univ.Sequence):
    componentType = namedtype.NamedTypes(
        namedtype.NamedType("r", univ.Integer()),
        namedtype.NamedType("s", univ.Integer()),
    )


class SPKIAlgorithmIdentifierRecord(univ.Sequence):
    componentType = namedtype.NamedTypes(
        namedtype.NamedType("algorithm", univ.ObjectIdentifier()),
        namedtype.OptionalNamedType("parameters", univ.Any()),
    )


class SPKIRecord(univ.Sequence):
    componentType = namedtype.NamedTypes(
        namedtype.NamedType("algorithm", SPKIAlgorithmIdentifierRecord()),
        namedtype.NamedType("subjectPublicKey", univ.BitString()),
    )


def derive_address(
    address: Union[str, bytes], key: Union[str, bytes], get_byte: bool = False
) -> Union[bytes, str]:
    """
    Derive given address using key.
    It's just like libplanet's `address.DeriveAddress(key)` function.

    :param address: Original address to make derived address
    :param key: Derive Key
    :param get_byte: If set this values to `True`, the return value is converted to checksum encoded string. default: False
    :return: Derived address. (Either bytes or str followed by `get_byte` flag)
    """
    # TODO: Error handling
    if address.startswith("0x"):
        address = address[2:]

    if type(address) == str:
        address = bytes.fromhex(address)

    if type(key) == str:
        key = bytes(key, "UTF-8")

    derived = hmac.new(key, address, sha1).digest()
    return derived if get_byte else checksum_encode(derived)


def checksum_encode(addr: bytes) -> str:  # Takes a 20-byte binary address as input
    """
    Convert input address to checksum encoded address without prefix "0x"
    See [ERC-55](https://eips.ethereum.org/EIPS/eip-55)

    :param addr: 20-bytes binary address
    :return: checksum encoded address as string
    """
    hex_addr = addr.hex()
    checksum_buffer = ""

    # Treat the hex address as ascii/utf-8 for keccak256 hashing
    hashed_address = eth_utils.keccak(text=hex_addr).hex()

    # Iterate over each character in the hex address
    for nibble_index, character in enumerate(hex_addr):
        if character in "0123456789":
            # We can't upper-case the decimal digits
            checksum_buffer += character
        elif character in "abcdef":
            # Check if the corresponding hex digit (nibble) in the hash is 8 or higher
            hashed_address_nibble = int(hashed_address[nibble_index], 16)
            if hashed_address_nibble > 7:
                checksum_buffer += character.upper()
            else:
                checksum_buffer += character
        else:
            raise eth_utils.ValidationError(
                f"Unrecognized hex character {character!r} at position {nibble_index}"
            )
    return checksum_buffer
