# Import tasks here for autodiscovery
from app.tasks.burn_asset_task import process_burn_asset
from app.tasks.claim_task import process_claim, process_retry_claim
from app.tasks.stage_task import process_retry_stage

__all__ = [
    "process_claim",
    "process_retry_claim",
    "process_retry_stage",
    "process_burn_asset",
]
