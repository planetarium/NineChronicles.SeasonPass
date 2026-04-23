import csv
import io
import time
from typing import Dict, Optional

import requests
import structlog
from shared.enums import PlanetID

logger = structlog.get_logger(__name__)

DEFAULT_COST_AP = 5
_CACHE_TTL = 3600  # 1 hour

_stage_cost_cache: Dict[str, Dict[int, int]] = {}  # planet_id -> {stage_id -> cost_ap}
_cache_timestamp: Dict[str, float] = {}  # planet_id -> last_fetched_time

CDN_BASE_URL = "https://sheets.planetarium.dev"


def _fetch_stage_sheet(planet_id: PlanetID) -> Dict[int, int]:
    """Fetch StageSheet from R2 CDN and return stage_id -> cost_ap mapping."""
    planet_key = planet_id.decode()
    url = f"{CDN_BASE_URL}/{planet_key}/StageSheet.csv"
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        reader = csv.DictReader(io.StringIO(resp.text))
        result = {}
        for row in reader:
            try:
                result[int(row["id"])] = int(row["cost_ap"])
            except (ValueError, KeyError):
                continue
        logger.info(
            f"Fetched StageSheet from CDN: {len(result)} stages loaded for {planet_key}"
        )
        return result
    except Exception as e:
        logger.warning(f"Failed to fetch StageSheet from CDN for {planet_key}: {e}")
        return {}


def get_stage_cost_ap(planet_id: PlanetID, stage_id: Optional[int]) -> int:
    """Get the CostAP for a given stage, using a cached StageSheet from CDN."""
    if stage_id is None:
        return DEFAULT_COST_AP

    planet_key = planet_id.decode()
    now = time.time()

    if (
        planet_key not in _stage_cost_cache
        or now - _cache_timestamp.get(planet_key, 0) > _CACHE_TTL
    ):
        sheet = _fetch_stage_sheet(planet_id)
        if sheet:
            _stage_cost_cache[planet_key] = sheet
            _cache_timestamp[planet_key] = now

    cost_ap = _stage_cost_cache.get(planet_key, {}).get(stage_id, DEFAULT_COST_AP)
    return cost_ap
