from __future__ import annotations

import hashlib
import hmac
from decimal import Decimal
from typing import Any, Dict, List, Optional
from uuid import uuid1

import bencodex
import eth_utils

__all__ = [
    "Address",
    "BurnAsset",
    "ClaimItems",
    "Currency",
    "FungibleAssetValue",
    "GrantItems",
]


class Address:
    def __init__(self, addr: str):
        if addr.startswith("0x"):
            if len(addr) != 42:
                raise ValueError("Address with 0x prefix must have exact 42 chars.")
            self.raw = bytes.fromhex(addr[2:])
        else:
            if len(addr) != 40:
                raise ValueError("Address without 0x prefix must have exact 40 chars.")
            self.raw = bytes.fromhex(addr)

    @property
    def long_format(self):
        return f"0x{self.raw.hex()}"

    @property
    def short_format(self):
        return self.raw.hex()

    def derive(self, key: str) -> Address:
        return Address(
            self.__checksum_encode(
                hmac.new(key.encode("utf-8"), self.raw, digestmod=hashlib.sha1).digest()
            )
        )

    def __checksum_encode(
        self, addr: bytes
    ) -> str:  # Takes a 20-byte binary address as input
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

    def __eq__(self, other: Address):
        return self.raw == other.raw


class Currency:
    """
    # Currency
    ---
    Lib9c Currency model which has ticker, minters, decimal_places, total_supply_trackable.
    `minters` will be automatically sanitized to `None` if empty list provided.
    """

    def __init__(
        self,
        ticker: str,
        decimal_places: int,
        minters: Optional[List[str]] = None,
        total_supply_trackable: bool = False,
    ):
        self.ticker = ticker
        self.minters = [Address(x) for x in minters] if minters else None
        self.decimal_places = decimal_places
        self.total_supply_trackable = total_supply_trackable

    def __eq__(self, other: Currency):
        return (
            self.ticker == other.ticker
            and self.minters == other.minters
            and self.decimal_places == other.decimal_places
            and self.total_supply_trackable == other.total_supply_trackable
        )

    @classmethod
    def NCG(cls):
        return cls(
            ticker="NCG",
            minters=["47d082a115c63e7b58b1532d20e631538eafadde"],
            decimal_places=2,
        )

    @classmethod
    def CRYSTAL(cls):
        return cls(ticker="CRYSTAL", minters=None, decimal_places=18)

    @property
    def plain_value(self) -> Dict[str, Any]:
        value = {
            "ticker": self.ticker,
            "decimalPlaces": chr(self.decimal_places).encode(),
            "minters": [x.raw for x in self.minters] if self.minters else None,
        }
        if self.total_supply_trackable:
            value["totalSupplyTrackable"] = True
        return value

    @property
    def serialized_plain_value(self) -> bytes:
        return bencodex.dumps(self.plain_value)


class FungibleAssetValue:
    def __init__(self, currency: Currency, amount: Decimal):
        self.currency = currency
        self.amount = amount

    def __eq__(self, other: FungibleAssetValue):
        return self.currency == other.currency and self.amount == other.amount

    @classmethod
    def from_raw_data(
        cls,
        ticker: str,
        decimal_places: int,
        minters: Optional[List[str]] = None,
        total_supply_trackable: bool = False,
        amount: Decimal = Decimal("0"),
    ):
        return cls(
            Currency(ticker, decimal_places, minters, total_supply_trackable),
            amount=amount,
        )

    @property
    def plain_value(self) -> List[Dict[str, Any] | int]:
        return [
            self.currency.plain_value,
            int(self.amount * max(1, 10**self.currency.decimal_places)),
        ]

    @property
    def serialized_plain_value(self) -> bytes:
        return bencodex.dumps(self.plain_value)


class ActionBase:
    def __init__(self, type_id: str, _id: Optional[str] = None, **kwargs):
        self._id = _id if _id else uuid1().hex
        self._type_id = type_id

    @property
    def plain_value(self):
        return {"type_id": self._type_id, "values": self._plain_value}

    @property
    def _plain_value(self):
        raise NotImplementedError

    @property
    def serialized_plain_value(self):
        return bencodex.dumps(self.plain_value)


class BurnAsset(ActionBase):
    TYPE_ID: str = "burn_asset"

    def __init__(
        self,
        *,
        owner: Address,
        amount: FungibleAssetValue,
        memo: str,
        _id: Optional[str] = None,
    ):
        super().__init__(self.TYPE_ID, _id)
        self._owner = owner
        self._amount = amount
        self._memo = memo

    @property
    def _plain_value(self):
        return [
            bytes.fromhex(self._owner.short_format),
            self._amount.plain_value,
            self._memo,
        ]


class ClaimItems(ActionBase):
    """
    Python port of `ClaimItems` action from lib9c

    - type_id : `claim_items`
    - values: List[claim_data]
      - claimData: List[claimItem]
        - claimItem: Dict[str, Address|List[FungibleAssetValue]]
          - valid keys: ["avatarAddress", "fungibleAssetValues"]
      - memo: JSON serialized string
    """

    TYPE_ID: str = "claim_items"

    def __init__(
        self,
        *,
        claim_data: List[Dict[str, Address | List[FungibleAssetValue]]],
        memo: Optional[str] = None,
        _id: Optional[str] = None,
    ):
        super().__init__(self.TYPE_ID, _id)
        self._claim_data = claim_data
        self._memo = memo

    @property
    def _plain_value(self):
        return {
            "id": bytes.fromhex(self._id),
            "cd": [
                [
                    cd["avatarAddress"].raw,
                    [x.plain_value for x in cd["fungibleAssetValues"]],
                ]
                for cd in self._claim_data
            ],
            "m": self._memo,
        }


class GrantItems(ActionBase):
    """
    Python port of `GrantItems` action from lib9c (PR #3257).

    - type_id : `grant_items`
    - values:
      - cd: List[(avatarAddress, fungibleAssetValues)]
      - m: optional memo string (omitted if empty)

    NOTE:
    - Unlike `ClaimItems`, `GrantItems` does NOT include an `id` field in values.
    """

    TYPE_ID: str = "grant_items"

    def __init__(
        self,
        *,
        claim_data: List[Dict[str, Address | List[FungibleAssetValue]]],
        memo: Optional[str] = None,
        _id: Optional[str] = None,
    ):
        super().__init__(self.TYPE_ID, _id)
        self._claim_data = claim_data
        self._memo = memo

    @property
    def _plain_value(self):
        pv: Dict[str, Any] = {
            "cd": [
                [
                    cd["avatarAddress"].raw,
                    [x.plain_value for x in cd["fungibleAssetValues"]],
                ]
                for cd in self._claim_data
            ],
        }
        if self._memo is not None and self._memo != "":
            pv["m"] = self._memo
        return pv
