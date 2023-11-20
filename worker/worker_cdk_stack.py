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
    aws_sqs as _sqs,
)
from constructs import Construct

from common import COMMON_LAMBDA_EXCLUDE
from worker import WORKER_LAMBDA_EXCLUDE

PLANET_ON_LINE = ("odin", "heimdall")


class WorkerStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        self.config = kwargs.pop("config")
        self.shared_stack = kwargs.pop("shared_stack", None)
        if self.shared_stack is None:
            raise ValueError("Shared stack not found. Please provide shared stack.")
        super().__init__(scope, construct_id, **kwargs)

        # Lambda Layer
        layer = _lambda.LayerVersion(
            self, f"{self.config.stage}-9c-season_pass-worker-lambda-layer",
            code=_lambda.AssetCode("worker/layer/"),
            description="Lambda layer for 9c SeasonPass Worker",
            compatible_runtimes=[
                _lambda.Runtime.PYTHON_3_11,
            ],
            removal_policy=RemovalPolicy.DESTROY,
        )

        # Environment variables
        env = {
            "REGION_NAME": self.config.region_name,
            "STAGE": self.config.stage,
            "SECRET_ARN": self.shared_stack.rds.secret.secret_arn,
            "DB_URI": f"postgresql://"
                      f"{self.shared_stack.credentials.username}:[DB_PASSWORD]"
                      f"@{self.shared_stack.rds.db_instance_endpoint_address}"
                      f"/season_pass",
            "SQS_URL": self.shared_stack.brave_q.queue_url,
            # This is not used, but for reference compatibility. This can be deleted once after the stack is deployed.
            "PLANET_URL": self.config.planet_url,
        }

        # Exclude list
        exclude_list = [".idea", ".gitignore", ]
        exclude_list.extend(COMMON_LAMBDA_EXCLUDE)
        exclude_list.extend(WORKER_LAMBDA_EXCLUDE)

        # Unloader Role
        unloader_role = _iam.Role(
            self, f"{self.config.stage}-9c-season_pass-unloader-role",
            assumed_by=_iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                _iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaVPCAccessExecutionRole"),
            ],
        )
        # This is just for stack compatibility
        unloader_role.add_to_policy(
            _iam.PolicyStatement(
                actions=["sqs:sendmessage"],
                resources=[
                    self.shared_stack.brave_q.queue_arn,
                ]
            )
        )
        unloader_role = self.__add_policy(unloader_role, db_password=True, kms=True)
        unloader = _lambda.Function(
            self, f"{self.config.stage}-9c-season_pass-unloader-function",
            function_name=f"{self.config.stage}-9c-season_pass-unloader",
            runtime=_lambda.Runtime.PYTHON_3_11,
            description="Reward unloader of NineChronicles.SeasonPass",
            code=_lambda.AssetCode("worker/", exclude=exclude_list),
            handler="unloader.handle",
            layers=[layer],
            role=unloader_role,
            vpc=self.shared_stack.vpc,
            timeout=cdk_core.Duration.seconds(120),
            environment=env,
            events=[
                _evt_src.SqsEventSource(self.shared_stack.unload_q)
            ],
            memory_size=256,
            reserved_concurrent_executions=1,
        )

        # Track blocks by planet
        # Every minute
        minute_event_rule = _events.Rule(
            self, f"{self.config.stage}-9c-season_pass-block_tracker-event",
            schedule=_events.Schedule.cron(minute="*")  # Every minute
        )

        resp = requests.get(self.config.planet_url)
        data = resp.json()

        print(f"{len(data)} Planets to track blocks: {[x['name'] for x in data]}")
        for planet in data:
            planet_name = planet["name"].split(" ")[0]
            if planet_name not in PLANET_ON_LINE:
                print(f"Planet {planet_name} is not on line. Skip.")
                continue

            brave_dlq = _sqs.Queue(self, f"{self.config.stage}-{planet_name}-9c-season_pass-brave-dlq")
            brave_q = _sqs.Queue(
                self, f"{self.config.stage}-{planet_name}-9c-season_pass-brave-queue",
                dead_letter_queue=_sqs.DeadLetterQueue(max_receive_count=2, queue=brave_dlq),
                visibility_timeout=cdk_core.Duration.seconds(120),
            )

            env["PLANET_ID"] = planet["id"]
            env["GQL_URL"] = planet["rpcEndpoints"]["headless.gql"][0]
            env["SQS_URL"] = brave_q.queue_url

            tracker_role = _iam.Role(
                self, f"{self.config.stage}-{planet_name}-9c-season_pass-tracker-role",
                assumed_by=_iam.ServicePrincipal("lambda.amazonaws.com"),
                managed_policies=[
                    _iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaVPCAccessExecutionRole"),
                ],
            )
            tracker_role.add_to_policy(
                _iam.PolicyStatement(
                    actions=["sqs:sendmessage"],
                    resources=[
                        brave_q.queue_arn,
                    ]
                )
            )

            block_tracker = _lambda.Function(
                self, f"{self.config.stage}-{planet_name}-9c-season_pass-block_tracker-function",
                function_name=f"{self.config.stage}-{planet_name}-9c-season_pass-block_tracker",
                runtime=_lambda.Runtime.PYTHON_3_11,
                description="Block tracker of NineChronicles.SeasonPass to send action data to brave_handler",
                code=_lambda.AssetCode("worker/", exclude=exclude_list),
                handler="block_tracker.handle",
                layers=[layer],
                role=tracker_role,
                vpc=self.shared_stack.vpc,
                timeout=cdk_core.Duration.seconds(70),  # NOTE: This must be longer than 1 minute
                environment=env,
                memory_size=128,
            )

            minute_event_rule.add_target(_event_targets.LambdaFunction(block_tracker))

            try:
                del env["PLANET_ID"]
                del env["GQL_URL"]
            except KeyError:
                pass

            handler_role = _iam.Role(
                self, f"{self.config.stage}-{planet_name}-9c-season_pass-handler-role",
                assumed_by=_iam.ServicePrincipal("lambda.amazonaws.com"),
                managed_policies=[
                    _iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaVPCAccessExecutionRole"),
                ],
            )
            self.__add_policy(handler_role, db_password=True)

            brave_handler = _lambda.Function(
                self, f"{self.config.stage}-{planet_name}-9c-season_pass-brave_handler-function",
                function_name=f"{self.config.stage}-{planet_name}-9c-season_pass-brave_handler",
                runtime=_lambda.Runtime.PYTHON_3_11,
                description="Brave exp handler of NineChronicles.SeasonPass",
                code=_lambda.AssetCode("worker/", exclude=exclude_list),
                handler="brave_handler.handle",
                layers=[layer],
                role=handler_role,
                vpc=self.shared_stack.vpc,
                timeout=cdk_core.Duration.seconds(120),
                environment=env,
                events=[
                    _evt_src.SqsEventSource(brave_q)
                ],
                memory_size=192,
            )

        # Manual signer
        manual_signer_role = _iam.Role(
            self, f"{self.config.stage}-9c-season_pass-signer-role",
            assumed_by=_iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                _iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaVPCAccessExecutionRole"),
            ],
        )
        self.__add_policy(manual_signer_role, kms=True)
        if self.config.stage != "mainnet":
            manual_signer = _lambda.Function(
                self, f"{self.config.stage}-9c-season_pass-manual_signer-function",
                function_name=f"{self.config.stage}-9c-season_pass-manual_signer",
                runtime=_lambda.Runtime.PYTHON_3_11,
                description="Manual Tx. signer from season pass garage address",
                code=_lambda.AssetCode("worker/", exclude=exclude_list),
                handler="manual_signer.handle",
                layers=[layer],
                role=manual_signer_role,
                vpc=self.shared_stack.vpc,
                timeout=cdk_core.Duration.seconds(10),
                environment=env,
            )

    def __add_policy(self, role: _iam.IRole, *, db_password: bool = False, kms: bool = False) -> _iam.IRole:
        if db_password:
            role.add_to_policy(
                _iam.PolicyStatement(
                    actions=["secretsmanager:GetSecretValue"],
                    resources=[self.shared_stack.rds.secret.secret_arn],
                )
            )
        if kms:
            ssm = boto3.client("ssm", region_name=self.config.region_name,
                               aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
                               aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
                               )
            resp = ssm.get_parameter(Name=f"{self.config.stage}_9c_SEASON_PASS_KMS_KEY_ID", WithDecryption=True)
            kms_key_id = resp["Parameter"]["Value"]
            role.add_to_policy(
                _iam.PolicyStatement(
                    actions=["kms:GetPublicKey", "kms:Sign"],
                    resources=[f"arn:aws:kms:{self.config.region_name}:{self.config.account_id}:key/{kms_key_id}"]
                )
            )
            role.add_to_policy(
                _iam.PolicyStatement(
                    actions=["ssm:GetParameter"],
                    resources=[
                        self.shared_stack.kms_key_id_arn,
                    ]
                )
            )

        return role
