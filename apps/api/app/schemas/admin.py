from decimal import Decimal

from pydantic import BaseModel


class BurnAssetRequest(BaseModel):
    ticker: str  # Currency ticker (e.g., "NCG", "CRYSTAL")
    amount: Decimal  # Amount to burn
    memo: str = ""  # Optional memo
    planet_id: str = "0x000000000000"  # Planet ID (기본값: ODIN)


class BurnAssetResponse(BaseModel):
    task_id: str
    status: str
    message: str
