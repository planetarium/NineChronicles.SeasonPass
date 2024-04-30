import os

DEBUG = True
LOG_LEVEL = "DEBUG"

DB_URI = os.environ.get("DB_URI")

JWT_TOKEN_SECRET = "secret"

# AWS
REGION_NAME = "ap-northeast-2"
SQS_URL = None

# Headless
HEADLESS_JWT_GQL_SECRET = os.environ.get("HEADLESS_JWT_GQL_SECRET")
