import os

import aws_cdk as cdk_core
import boto3
from aws_cdk import (
    Stack, RemovalPolicy,
    aws_lambda as _lambda,
    aws_iam as _iam,
    aws_ec2 as _ec2,
    aws_events as _events,
    aws_events_targets as _event_targets,
    aws_lambda_event_sources as _evt_src,
)
from constructs import Construct

from common import COMMON_LAMBDA_EXCLUDE
from worker import WORKER_LAMBDA_EXCLUDE


class WorkerStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        self.config = kwargs.pop("config")
        self.shared_stack = kwargs.pop("shared_stack", None)
        if self.shared_stack is None:
            raise ValueError("Shared stack not found. Please provide shared stack.")
        super().__init__(scope, construct_id, **kwargs)

        # EC2 block tracker
        init_file = ".github/scripts/init_block_tracker.sh"
        with open(init_file, "r") as f:
            s = f.read()
        s.replace("[BRANCH]", os.popen("git branch --show-current").read())
        with open(init_file, "w") as f:
            f.write(s)

        tracker_role = _iam.Role(
            self, f"{self.config.stage}-9c-season_pass-tracker-role",
            assumed_by=_iam.ServicePrincipal("ec2.amazonaws.com"),
        )
        self.shared_stack.brave_q.grant_send_messages(tracker_role)
        self.shared_stack.adventure_boss_q.grant_send_messages(tracker_role)
        self.shared_stack.world_clear_q.grant_send_messages(tracker_role)
        self.__add_policy(tracker_role, db_password=True)

        if self.config.stage == "mainnet":
            instance_type = _ec2.InstanceType.of(_ec2.InstanceClass.M6G, _ec2.InstanceSize.LARGE)
            ami = _ec2.MachineImage.lookup(
                name="9c_season-pass_block-tracker_20241128",
            )
        elif self.config.stage in ("internal", "preview"):
            instance_type = _ec2.InstanceType.of(_ec2.InstanceClass.BURSTABLE4_GRAVITON, _ec2.InstanceSize.SMALL)
            ami = _ec2.MachineImage.lookup(
                name="internal-season_pass-20241115",
            )
        else:
            instance_type = _ec2.InstanceType.of(_ec2.InstanceClass.BURSTABLE4_GRAVITON, _ec2.InstanceSize.SMALL)
            ami = _ec2.MachineImage.from_ssm_parameter(
                "/aws/service/canonical/ubuntu/server/jammy/stable/current/arm64/hvm/ebs-gp2/ami-id"
            )

        block_tracker = _ec2.Instance(
            self, f"{self.config.stage}-9c-season_pass-block_tracker",
            vpc=self.shared_stack.vpc,
            # availability_zone="us-east-2c",
            # vpc_subnets=_ec2.SubnetSelection(subnet_type=_ec2.SubnetType.PRIVATE_WITH_EGRESS),
            instance_name=f"{self.config.stage}-9c-season_pass-block_tracker",
            instance_type=instance_type,
            machine_image=ami,
            key_name=self.shared_stack.resource_data.key_name,
            security_group=self.shared_stack.ec2_sg,
            user_data=_ec2.UserData.for_linux().add_execute_file_command(file_path=init_file),
            role=tracker_role,
            vpc_subnets=_ec2.SubnetSelection(
                availability_zones=[f"{self.config.region_name}c"],
                subnet_type=_ec2.SubnetType.PRIVATE_WITH_EGRESS
            ),
        )

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
                      f"@{self.shared_stack.rds_endpoint}"
                      f"/season_pass",
            "COURAGE_Q_URL": self.shared_stack.brave_q.queue_url,
            "ADV_BOSS_Q_URL": self.shared_stack.adventure_boss_q.queue_url,
            "WORLD_CLEAR_Q_URL": self.shared_stack.world_clear_q.queue_url,
            # This is not used, but for reference compatibility. This can be deleted once after the stack is deployed.
            "ODIN_GQL_URL": self.config.odin_gql_url,
            "HEIMDALL_GQL_URL": self.config.heimdall_gql_url,
            "THOR_GQL_URL": self.config.thor_gql_url,
            "HEADLESS_GQL_JWT_SECRET": self.config.headless_gql_jwt_secret,
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
            timeout=cdk_core.Duration.seconds(15),
            environment=env,
            events=[
                _evt_src.SqsEventSource(self.shared_stack.unload_q)
            ],
            memory_size=1024,
            reserved_concurrent_executions=1,
        )

        handler_role = _iam.Role(
            self, f"{self.config.stage}-9c-season_pass-handler-role",
            assumed_by=_iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                _iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaVPCAccessExecutionRole"),
            ],
        )
        self.__add_policy(handler_role, db_password=True)

        courage_handler = _lambda.Function(
            self, f"{self.config.stage}-9c-season_pass-brave_handler-function",
            function_name=f"{self.config.stage}-9c-season_pass-brave_handler",
            runtime=_lambda.Runtime.PYTHON_3_11,
            description="Brave exp handler of NineChronicles.SeasonPass",
            code=_lambda.AssetCode("worker/", exclude=exclude_list),
            handler="handler.courage_handler.handle",
            layers=[layer],
            role=handler_role,
            vpc=self.shared_stack.vpc,
            timeout=cdk_core.Duration.seconds(15),
            environment=env,
            events=[
                _evt_src.SqsEventSource(self.shared_stack.brave_q)
            ],
            memory_size=256,
        )

        adv_boss_handler = _lambda.Function(
            self, f"{self.config.stage}-9c-season_pass-adv_boss_handler-function",
            function_name=f"{self.config.stage}-9c-season_pass-adv_boss_handler",
            runtime=_lambda.Runtime.PYTHON_3_11,
            description="Adventure boss handler of NineChronicles.SeasonPass",
            code=_lambda.AssetCode("worker/", exclude=exclude_list),
            handler="handler.adventure_boss_handler.handle",
            layers=[layer],
            role=handler_role,
            vpc=self.shared_stack.vpc,
            timeout=cdk_core.Duration.seconds(15),
            environment=env,
            events=[
                _evt_src.SqsEventSource(self.shared_stack.adventure_boss_q)
            ],
            memory_size=256,
        )

        world_clear_handler = _lambda.Function(
            self, f"{self.config.stage}-9c-season_pass-world_clear_handler-function",
            function_name=f"{self.config.stage}-9c-season_pass-world_clear_handler",
            runtime=_lambda.Runtime.PYTHON_3_11,
            description="Adventure boss handler of NineChronicles.SeasonPass",
            code=_lambda.AssetCode("worker/", exclude=exclude_list),
            handler="handler.world_clear_handler.handle",
            layers=[layer],
            role=handler_role,
            vpc=self.shared_stack.vpc,
            timeout=cdk_core.Duration.seconds(15),
            environment=env,
            events=[
                _evt_src.SqsEventSource(self.shared_stack.world_clear_q)
            ],
            memory_size=256,
        )

        # Tracker Lambda Function
        tx_tracker_role = _iam.Role(
            self, f"{self.config.stage}-9c-season_pass-tx_tracker-role",
            assumed_by=_iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                _iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaVPCAccessExecutionRole"),
            ],
        )
        self.__add_policy(tx_tracker_role, db_password=True)

        tracker = _lambda.Function(
            self, f"{self.config.stage}-9c-season_pass-tracker-function",
            function_name=f"{self.config.stage}-9c-season_pass-tx-tracker",
            runtime=_lambda.Runtime.PYTHON_3_11,
            description="9c transaction status tracker of NineChronicles.SeasonPass",
            code=_lambda.AssetCode("worker/", exclude=exclude_list),
            handler="tx_tracker.track_tx",
            layers=[layer],
            role=tx_tracker_role,
            vpc=self.shared_stack.vpc,
            timeout=cdk_core.Duration.seconds(50),
            memory_size=1024,
            environment=env,
        )

        # Every minute
        minute_event_rule = _events.Rule(
            self, f"{self.config.stage}-9c-season_pass-tracker-event",
            schedule=_events.Schedule.cron(minute="*")  # Every minute
        )
        minute_event_rule.add_target(_event_targets.LambdaFunction(tracker))

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
                timeout=cdk_core.Duration.seconds(150),
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
