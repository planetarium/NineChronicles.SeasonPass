import os

import requests

from common.utils.season_pass import create_jwt_token


def get_block_tip(url: str):
    resp = requests.post(
        url,
        json={"query": "{ nodeStatus { tip { index } } }"},
        headers={"Authorization": f"Bearer {create_jwt_token(os.environ.get('HEADLESS_GQL_JWT_SECRET'))}"}
    )
    return resp.json()["data"]["nodeStatus"]["tip"]["index"]
