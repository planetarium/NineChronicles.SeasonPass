import os

import requests

from common.utils._crypto import Account
from common.utils.aws import fetch_kms_key_id

stage = os.environ.get("STAGE", "development")
region_name = os.environ.get("REGION_NAME", "us-east-2")
url = ""


def handle(event, context):
    account = Account(fetch_kms_key_id(stage, region_name))

    # Sign and stage transaction
    for x in event:
        utx = bytes.fromhex(x)
        sig = account.sign_tx(utx).hex()

        # NOTE: If you want to get just signature, use this:
        # return account.sign_tx(utx).hex()

        # NOTE: If you want to sign and stage transaction, use this:
        q = f"""{{ transaction {{ signTransaction(
            unsignedTransaction: "{x}", signature: "{sig}"
            ) }} }}"""

        resp = requests.post(url, json={"query": q})
        stx = resp.json()["data"]["transaction"]["signTransaction"]

        q = f"""mutation {{stageTransaction(payload: "{stx}")}}"""
        resp = requests.post(url, json={"query": q})
        print(resp.json()["data"]["stageTransaction"])
