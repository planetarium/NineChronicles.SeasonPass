import logging
import os

from starlette.config import Config

stage = os.environ.get("STAGE", "local")
db_password = None

if not stage:
    logging.error("Config file not found")
    raise FileNotFoundError(f"No config file found for stage {stage}")

if os.path.exists(os.path.join("season_pass", "settings", f"{stage}.py")):
    env_module = __import__(f"season_pass.settings.{stage}", fromlist=["season_pass.settings"])
    envs = {k: v for k, v in env_module.__dict__.items() if k.upper() == k}
    config = Config(environ=envs)
else:
    config = Config(environ=os.environ)

# Prepare settings values
DEBUG = config("DEBUG", cast=bool, default=False)
LOGGING_LEVEL = logging.getLevelName(config("LOGGING_LEVEL", default="INFO"))
DB_URI = config("DB_URI")
DB_ECHO = config("DB_ECHO", cast=bool, default=False)

JWT_TOKEN_SECRET = config("JWT_TOKEN_SECRET")

# AWS
REGION_NAME = config("REGION_NAME")
SQS_URL = config("SQS_URL")

# JWT Headless
HEADLESS_GQL_JWT_SECRET = config("HEADLESS_GQL_JWT_SECRET")
