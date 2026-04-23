import csv
import io
from typing import Dict, Optional

import requests
import structlog
from shared.enums import PlanetID

logger = structlog.get_logger(__name__)

DEFAULT_COST_AP = 5

_stage_cost_cache: Dict[str, Dict[int, int]] = {}  # planet_id -> {stage_id -> cost_ap}
_etag_cache: Dict[str, str] = {}  # planet_id -> etag

CDN_BASE_URL = "https://sheets.planetarium.dev"


def _fetch_stage_sheet(planet_id: PlanetID) -> Optional[Dict[int, int]]:
    """Fetch StageSheet from R2 CDN if changed. Returns None when not modified."""
    planet_key = planet_id.decode()
    url = f"{CDN_BASE_URL}/{planet_key}/StageSheet.csv"
    headers = {}
    cached_etag = _etag_cache.get(planet_key)
    if cached_etag:
        headers["If-None-Match"] = cached_etag
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 304:
            return None
        resp.raise_for_status()
        etag = resp.headers.get("ETag")
        if etag:
            _etag_cache[planet_key] = etag
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
        return None


def get_stage_cost_ap(planet_id: PlanetID, stage_id: Optional[int]) -> int:
    """Get the CostAP for a given stage, using a cached StageSheet from CDN."""
    if stage_id is None:
        return DEFAULT_COST_AP

    planet_key = planet_id.decode()

    if planet_key not in _stage_cost_cache:
        sheet = _fetch_stage_sheet(planet_id)
        if sheet:
            _stage_cost_cache[planet_key] = sheet

    cost_ap = _stage_cost_cache.get(planet_key, {}).get(stage_id, DEFAULT_COST_AP)
    return cost_ap


def refresh_stage_sheets():
    """Re-fetch StageSheet for all cached planets if CDN content changed."""
    for planet_key in list(_stage_cost_cache.keys()):
        planet_id = PlanetID(planet_key.encode())
        sheet = _fetch_stage_sheet(planet_id)
        if sheet is not None:
            _stage_cost_cache[planet_key] = sheet
