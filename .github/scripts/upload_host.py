import logging
import os

import boto3
import sys

if __name__ == "__main__":
    host = sys.argv[1]
    ssm = boto3.client("ssm", region_name=os.environ.get("RERGION_NAME"),
                       aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
                       aws_secret_access_key=os.environ.get('AWS_SECRET_ACCESS_KEY')
                       )
    try:
        ssm.put_parameter(
            Name=f"{os.environ.get('STAGE')}_9c_SEASON_PASS_HOST",
            Value=host,
            Type="string",
            Overwrite=True,
        )
    except Exception as e:
        logging.error(f"Set SeasonPass Host to Parameter Store Failed: {e}")
        exit(1)
