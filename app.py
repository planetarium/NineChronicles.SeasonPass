#!/usr/bin/env python3
import logging
import os

import aws_cdk as cdk
from dotenv import dotenv_values

from common import Config
from common.shared_cdk_stack import SharedStack
from season_pass.api_cdk_stack import APIStack
from worker.worker_cdk_stack import WorkerStack

stage = os.environ.get("STAGE", "development")

if os.path.exists(f".env.{stage}"):
    env_values = dotenv_values(f".env.{stage}")
    if stage != env_values["STAGE"]:
        logging.error(f"Provided stage {stage} is not identical with STAGE in env: {env_values['STAGE']}")
        exit(1)
else:
    env_values = os.environ

config = Config(**{k.lower(): v for k, v in env_values.items()})

TAGS = {
    "Name": f"9c-season_pass-{stage}",
    "Environment": "production" if stage == "mainnet" else "development",
    "Service": "NineChronicles.SeasonPass",
    "Team": "game",
    "Owner": "hyeon",
}

app = cdk.App()
shared = SharedStack(
    app, f"{config.stage}-9c-season-pass-SharedStack",
    env=cdk.Environment(
        account=config.account_id, region=config.region_name,
    ),
    config=config,
    tags=TAGS,
)

APIStack(
    app, f"{config.stage}-9c-season-pass-APIStack",
    env=cdk.Environment(
        account=config.account_id, region=config.region_name,
    ),
    config=config,
    shared_stack=shared,
    tags=TAGS,
)

WorkerStack(
    app, f"{config.stage}-9c-season-pass-WorkerStack",
    env=cdk.Environment(
        account=config.account_id, region=config.region_name,
    ),
    config=config,
    shared_stack=shared,
    tags=TAGS,
)

app.synth()
