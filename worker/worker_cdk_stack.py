import os

import aws_cdk as cdk_core
import boto3
import requests
from aws_cdk import (
    Stack, RemovalPolicy,
    aws_lambda as _lambda,
    aws_iam as _iam,
    aws_lambda_event_sources as _evt_src,
    aws_events as _events,
    aws_events_targets as _event_targets,
)
from constructs import Construct

from common import Config, COMMON_LAMBDA_EXCLUDE
from worker import WORKER_LAMBDA_EXCLUDE


class WorkerStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        config: Config = kwargs.pop("config")
        shared_stack = kwargs.pop("shared_stack", None)
        if shared_stack is None:
            raise ValueError("Shared stack not found. Please provide shared stack.")
        super().__init__(scope, construct_id, **kwargs)

        # Lambda Layer
        layer = _lambda.LayerVersion(
            self, f"{config.stage}-9c-season_pass-worker-lambda-layer",
            code=_lambda.AssetCode("worker/layer/"),
            description="Lambda layer for 9c SeasonPass Worker",
            compatible_runtimes=[
                _lambda.Runtime.PYTHON_3_11,
            ],
            removal_policy=RemovalPolicy.DESTROY,
        )

        # Lambda Role
        role = _iam.Role(
            self, f"{config.stage}-9c-season_pass-worker-role",
            assumed_by=_iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                _iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaVPCAccessExecutionRole"),
            ]
        )
        # DB Password
        role.add_to_policy(
            _iam.PolicyStatement(
                actions=["secretsmanager:GetSecretValue"],
                resources=[shared_stack.rds.secret.secret_arn],
            )
        )
        # KMS
        ssm = boto3.client("ssm", region_name=config.region_name,
                           aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
                           aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
                           )
        resp = ssm.get_parameter(Name=f"{config.stage}_9c_SEASON_PASS_KMS_KEY_ID", WithDecryption=True)
        kms_key_id = resp["Parameter"]["Value"]
        role.add_to_policy(
            _iam.PolicyStatement(
                actions=["kms:GetPublicKey", "kms:Sign"],
                resources=[f"arn:aws:kms:{config.region_name}:{config.account_id}:key/{kms_key_id}"]
            )
        )
        role.add_to_policy(
            _iam.PolicyStatement(
                actions=["ssm:GetParameter"],
                resources=[
                    shared_stack.kms_key_id_arn,
                ]
            )
        )
        role.add_to_policy(
            _iam.PolicyStatement(
                actions=["sqs:sendmessage"],
                resources=[
                    shared_stack.brave_q.queue_arn,
                ]
            )
        )

        # Environment variables
        env = {
            "REGION_NAME": config.region_name,
            "STAGE": config.stage,
            "SECRET_ARN": shared_stack.rds.secret.secret_arn,
            "DB_URI": f"postgresql://"
                      f"{shared_stack.credentials.username}:[DB_PASSWORD]"
                      f"@{shared_stack.rds.db_instance_endpoint_address}"
                      f"/season_pass",
            "SQS_URL": shared_stack.brave_q.queue_url,
        }

        # Exclude list
        exclude_list = [".idea", ".gitignore", ]
        exclude_list.extend(COMMON_LAMBDA_EXCLUDE)
        exclude_list.extend(WORKER_LAMBDA_EXCLUDE)

        unloader = _lambda.Function(
            self, f"{config.stage}-9c-season_pass-unloader-function",
            function_name=f"{config.stage}-9c-season_pass-unloader",
            runtime=_lambda.Runtime.PYTHON_3_11,
            description="Reward unloader of NineChronicles.SeasonPass",
            code=_lambda.AssetCode("worker/", exclude=exclude_list),
            handler="unloader.handle",
            layers=[layer],
            role=role,
            vpc=shared_stack.vpc,
            timeout=cdk_core.Duration.seconds(120),
            environment=env,
            events=[
                _evt_src.SqsEventSource(shared_stack.unload_q)
            ],
            memory_size=256,
            reserved_concurrent_executions=1,
        )

        # Track blocks by planet
        # Every minute
        minute_event_rule = _events.Rule(
            self, f"{config.stage}-9c-season_pass-block_tracker-event",
            schedule=_events.Schedule.cron(minute="*")  # Every minute
        )

        resp = requests.get(os.environ.get("PLANET_URL"))
        data = resp.json()
        print(f"{len(data)} Planets to track blocks: {[x['name'] for x in data]}")
        for planet in data:
            planet_name = planet["name"].split(" ")[0]
            env["PLANET_ID"] = planet["id"]
            env["GQL_URL"] = planet["rpcEndpoints"]["headless.gql"][0]

            block_tracker = _lambda.Function(
                self, f"{config.stage}-{planet_name}-9c-season_pass-block_tracker-function",
                function_name=f"{config.stage}-{planet_name}-9c-season_pass-block_tracker",
                runtime=_lambda.Runtime.PYTHON_3_11,
                description="Block tracker of NineChronicles.SeasonPass to send action data to brave_handler",
                code=_lambda.AssetCode("worker/", exclude=exclude_list),
                handler="block_tracker.handle",
                layers=[layer],
                role=role,
                vpc=shared_stack.vpc,
                timeout=cdk_core.Duration.seconds(75),  # NOTE: This must be longer than 1 minute
                environment=env,
                memory_size=512,
            )

            minute_event_rule.add_target(_event_targets.LambdaFunction(block_tracker))

        try:
            del env["PLANET_ID"]
            del env["GQL_URL"]
        except KeyError:
            pass

        brave_handler = _lambda.Function(
            self, f"{config.stage}-9c-season_pass-brave_handler-function",
            function_name=f"{config.stage}-9c-season_pass-brave_handler",
            runtime=_lambda.Runtime.PYTHON_3_11,
            description="Brave exp handler of NineChronicles.SeasonPass",
            code=_lambda.AssetCode("worker/", exclude=exclude_list),
            handler="brave_handler.handle",
            layers=[layer],
            role=role,
            vpc=shared_stack.vpc,
            timeout=cdk_core.Duration.seconds(120),
            environment={**env, "PLANET_URL": os.environ.get("PLANET_URL")},
            events=[
                _evt_src.SqsEventSource(shared_stack.brave_q)
            ],
            memory_size=256,
        )

        # Manual signer
        if config.stage != "mainnet":
            manual_signer = _lambda.Function(
                self, f"{config.stage}-9c-season_pass-manual_signer-function",
                function_name=f"{config.stage}-9c-season_pass-manual_signer",
                runtime=_lambda.Runtime.PYTHON_3_11,
                description="Manual Tx. signer from season pass garage address",
                code=_lambda.AssetCode("worker/", exclude=exclude_list),
                handler="manual_signer.handle",
                layers=[layer],
                role=role,
                vpc=shared_stack.vpc,
                timeout=cdk_core.Duration.seconds(10),
                environment=env,
            )
