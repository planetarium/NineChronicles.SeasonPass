import os

from common.utils._crypto import Account
from common.utils.aws import fetch_kms_key_id

stage = os.environ.get("STAGE", "development")
region_name = os.environ.get("REGION_NAME", "us-east-2")


def handle(event, context):
    account = Account(fetch_kms_key_id(stage, region_name))
    utx = bytes.fromhex(event)
    return account.sign_tx(utx).hex()
