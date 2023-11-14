import hashlib
import hmac
import logging
import os
from base64 import b64decode
from hashlib import sha1
from typing import Tuple, Union

import boto3
import eth_utils
from Crypto.Hash import keccak
from botocore.exceptions import ClientError
from eth_account import Account as EthAccount
from eth_utils import to_checksum_address
from pyasn1.codec.der.decoder import decode as der_decode
from pyasn1.codec.der.encoder import encode as der_encode
from pyasn1.type import namedtype, univ
from pyasn1.type.univ import SequenceOf, Integer


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


class Account:
    def __init__(self, kms_key: str):
        self.client = boto3.client("kms", region_name=os.environ.get("REGION_NAME"))  # specify region
        self._kms_key: str = kms_key
        try:
            self.pubkey_der: bytes = self.client.get_public_key(KeyId=self._kms_key)["PublicKey"]
            self.address: str = self.__der_encoded_public_key_to_eth_address(self.pubkey_der)
        except ClientError as e:
            logging.error(f"An error occurred getting KMS Key with Key ID \"{self._kms_key}\.")
            logging.error(e)
            raise e

        record, _ = der_decode(self.pubkey_der, asn1Spec=SPKIRecord())
        self.pubkey: bytes = record["subjectPublicKey"].asOctets()

    def get_item_garage_addr(self, item_id: str):
        return derive_address(derive_address(self.address, "garage"), item_id)

    def __public_key_int_to_eth_address(self, pubkey: int) -> str:
        """
        Given an integer public key, calculate the ethereum address.
        """
        hex_string = hex(pubkey).replace("0x", "")
        padded_hex_string = hex_string.replace("0x", "").zfill(130)[2:]

        k = keccak.new(digest_bits=256)
        k.update(bytes.fromhex(padded_hex_string))
        return to_checksum_address(bytes.fromhex(k.hexdigest())[-20:].hex())

    def __der_encoded_public_key_to_eth_address(self, pubkey: bytes) -> str:
        """
        Given a KMS Public Key, calculate the ethereum address.
        """
        received_record, _ = der_decode(pubkey, asn1Spec=SPKIRecord())
        return self.__public_key_int_to_eth_address(
            int(received_record["subjectPublicKey"].asBinary(), 2)
        )

    def __get_sig_r_s(self, signature: bytes) -> Tuple[int, int]:
        """
        Given a KMS signature, calculate r and s.
        """
        received_record, _ = der_decode(signature, asn1Spec=ECDSASignatureRecord())
        r = int(received_record["r"].prettyPrint())
        s = int(received_record["s"].prettyPrint())

        max_value_on_curve = (
            0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
        )

        if 2 * s >= max_value_on_curve:
            # s is on wrong side of curve, flip it
            s = max_value_on_curve - s
        return r, s

    def __get_sig_v(self, msg_hash: bytes, r: int, s: int, expected_address: str) -> int:
        """
        Given a message hash, r, s and an ethereum address, recover the
        recovery parameter v.
        """
        acc = EthAccount()
        recovered = acc._recover_hash(msg_hash, vrs=(27, r, s))
        recovered2 = acc._recover_hash(msg_hash, vrs=(28, r, s))
        expected_checksum_address = to_checksum_address(expected_address)

        if recovered == expected_checksum_address:
            return 0
        elif recovered2 == expected_checksum_address:
            return 1

        raise ValueError("Invalid Signature, cannot compute v, addresses do not match!")

    def __get_sig_r_s_v(self,
                        msg_hash: bytes, signature: bytes, address: str
                        ) -> Tuple[int, int, int]:
        """
        Given a message hash, a KMS signature and an ethereum address calculate r,
        s, and v.
        """
        r, s = self.__get_sig_r_s(signature)
        v = self.__get_sig_v(msg_hash, r, s, address)
        return r, s, v

    def __sign_msg_hash(self, msg_hash: bytes) -> Tuple[int, int, int]:
        signature = self.client.sign(
            KeyId=self._kms_key,
            Message=msg_hash,
            MessageType="DIGEST",
            SigningAlgorithm="ECDSA_SHA_256",
        )
        act_signature = signature["Signature"]
        return self.__get_sig_r_s_v(msg_hash, act_signature, self.address)

    def sign_tx(self, unsigned_tx: bytes) -> bytes:
        msg_hash = hashlib.sha256(unsigned_tx).digest()
        r, s, _ = self.__sign_msg_hash(msg_hash)

        n = int.from_bytes(
            b64decode("/////////////////////rqu3OavSKA7v9JejNA2QUE="), "big"
        )
        seq = SequenceOf(componentType=Integer())
        seq.extend([r, min(s, n - s)])
        return der_encode(seq)


def derive_address(address: Union[str, bytes], key: Union[str, bytes], get_byte: bool = False) -> Union[bytes, str]:
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
