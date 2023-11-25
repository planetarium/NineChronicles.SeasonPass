import aws_cdk as cdk_core
from aws_cdk import (
    RemovalPolicy, Stack,
    aws_apigateway as _apig,
    aws_iam as _iam,
    aws_lambda as _lambda,
)
from constructs import Construct

from common import COMMON_LAMBDA_EXCLUDE, Config
from season_pass import API_LAMBDA_EXCLUDE


class APIStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        config: Config = kwargs.pop("config")
        shared_stack = kwargs.pop("shared_stack", None)
        if shared_stack is None:
            raise ValueError("Shared stack not found. Please provide shared stack.")
        super().__init__(scope, construct_id, **kwargs)

        # Lambda Layer
        layer = _lambda.LayerVersion(
            self, f"{config.stage}-9c-season_pass-api-lambda-layer",
            code=_lambda.AssetCode("season_pass/layer/"),
            description="Lambda layer for 9c SeasonPass API Service",
            compatible_runtimes=[
                _lambda.Runtime.PYTHON_3_11,
            ],
            removal_policy=RemovalPolicy.DESTROY,
        )

        # Lambda Role
        role = _iam.Role(
            self, f"{config.stage}-9c-season_pass-api-role",
            assumed_by=_iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                _iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaVPCAccessExecutionRole"),
            ],
        )
        role.add_to_policy(
            _iam.PolicyStatement(
                actions=["secretsmanager:GetSecretValue"],
                resources=[shared_stack.rds.secret.secret_arn],
            )
        )
        role.add_to_policy(
            _iam.PolicyStatement(
                actions=["ssm:GetParameter"],
                resources=[
                    shared_stack.jwt_token_secret_arn,
                ]
            )
        )
        role.add_to_policy(
            _iam.PolicyStatement(
                actions=["sqs:sendmessage"],
                resources=[
                    shared_stack.unload_q.queue_arn,
                ]
            )
        )

        # Environment Variables
        env = {
            "STAGE": config.stage,
            "REGION_NAME": config.region_name,
            "SECRET_ARN": shared_stack.rds.secret.secret_arn,
            "DB_URI": f"postgresql://"
                      f"{shared_stack.credentials.username}:[DB_PASSWORD]"
                      f"@{shared_stack.rds_endpoint}"
                      f"/season_pass",
            "LOGGING_LEVEL": "INFO",
            "DB_ECHO": "False",
            "SQS_URL": shared_stack.unload_q.queue_url,
            "ODIN_VALIDATOR_URL": config.odin_validator_url,
            "HEIMDALL_VALIDATOR_URL": config.heimdall_validator_url,
        }

        # Lambda Function
        exclude_list = [".", "*", ".idea", ".git", ".pytest_cache", ".gitignore", ".github", ]
        exclude_list.extend(COMMON_LAMBDA_EXCLUDE)
        exclude_list.extend(API_LAMBDA_EXCLUDE)

        function = _lambda.Function(
            self, f"{config.stage}-9c-season_pass-api-function",
            runtime=_lambda.Runtime.PYTHON_3_11,
            function_name=f"{config.stage}-9c-season_pass-api",
            description="HTTP API/Backoffice service of NineChronicles.SeasonPass",
            code=_lambda.AssetCode(".", exclude=exclude_list),
            handler="season_pass.main.handler",
            layers=[layer],
            role=role,
            vpc=shared_stack.vpc,
            security_groups=[shared_stack.rds_security_group],
            timeout=cdk_core.Duration.seconds(10),
            environment=env,
            memory_size=256,
        )

        # API Gateway
        apig = _apig.LambdaRestApi(
            self, f"{config.stage}-9c_season_pass-api-apig",
            handler=function,
            deploy_options=_apig.StageOptions(stage_name=config.stage),
        )
