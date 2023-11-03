import os

import aws_cdk as cdk_core
import boto3
from aws_cdk import (
    Stack, RemovalPolicy,
    aws_lambda as _lambda,
    aws_iam as _iam,
    aws_ec2 as _ec2,
    aws_lambda_event_sources as _evt_src,
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

        # EC2 block tracker
        init_file = ".github/scripts/init_block_tracker.sh"
        with open(init_file, "r") as f:
            s = f.read()
        s.replace("[BRANCH]", os.popen("git branch --show-current").read())
        with open(init_file, "w") as f:
            f.write(s)

        block_tracker = _ec2.Instance(
            self, f"{config.stage}-9c-season_pass-block_tracker",
            vpc=shared_stack.vpc,
            instance_name=f"{config.stage}-9c-season_pass-block_tracker",
            instance_type=_ec2.InstanceType.of(_ec2.InstanceClass.BURSTABLE4_GRAVITON, _ec2.InstanceSize.SMALL),
            machine_image=_ec2.MachineImage.from_ssm_parameter(
                "/aws/service/canonical/ubuntu/server/jammy/stable/current/arm64/hvm/ebs-gp2/ami-id"
            ),
            key_name=shared_stack.resource_data.key_name,
            security_group=_ec2.SecurityGroup.from_lookup_by_id(
                self, f"{config.stage}-9c-season_pass-block_tracker-sg",
                security_group_id=shared_stack.resource_data.sg_id,
            ),
            user_data=_ec2.UserData.for_linux().add_execute_file_command(file_path=init_file)
        )

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
            self, f"{config.stage}-9c-iap-worker-role",
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

        # Environment variables
        env = {
            "REGION_NAME": config.region_name,
            "STAGE": config.stage,
            "SECRET_ARN": shared_stack.rds.secret.secret_arn,
            "DB_URI": f"postgresql://"
                      f"{shared_stack.credentials.username}:[DB_PASSWORD]"
                      f"@{shared_stack.rds.db_instance_endpoint_address}"
                      f"/season_pass",
        }

        # Exclude list
        exclude_list = [".idea", ".gitignore", ]
        exclude_list.extend(COMMON_LAMBDA_EXCLUDE)
        exclude_list.extend(WORKER_LAMBDA_EXCLUDE)

        # Unloader Lambda function
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
                _evt_src.SqsEventSource(shared_stack.q)
            ],
            memory_size=256,
            reserved_concurrent_executions=1,
        )
