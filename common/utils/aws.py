import json
import logging
from typing import Dict, Optional

import boto3


def fetch_parameter(region: str, parameter_name: str, secure: bool):
    ssm = boto3.client("ssm", region_name=region)
    resp = ssm.get_parameter(
        Name=parameter_name,
        WithDecryption=secure,
    )
    return resp["Parameter"]


def fetch_secrets(region: str, secret_arn: str) -> Dict:
    sm = boto3.client("secretsmanager", region_name=region)
    resp = sm.get_secret_value(SecretId=secret_arn)
    return json.loads(resp["SecretString"])


def fetch_kms_key_id(stage: str, region: str) -> Optional[str]:
    client = boto3.client("ssm", region_name=region)
    try:
        return client.get_parameter(Name=f"{stage}_9c_IAP_KMS_KEY_ID", WithDecryption=True)["Parameter"]["Value"]
    except Exception as e:
        logging.error(e)
        return None
