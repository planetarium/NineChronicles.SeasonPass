import logging
import os
from dataclasses import dataclass
from typing import Dict

import aws_cdk as cdk_core
import boto3
from aws_cdk import (
    Stack,
    aws_ec2 as _ec2,
    aws_rds as _rds,
    aws_sqs as _sqs,
)
from constructs import Construct

from common import Config
from common.utils.aws import fetch_parameter


@dataclass
class ResourceDict:
    vpc_id: str
    key_name: str
    sg_id: str


RESOURCE_DICT: Dict[str, ResourceDict] = {
    "development": ResourceDict(
        vpc_id="vpc-0cf2339a10213911d",  # Test VPC in AWS Dev Account - apne2 region
        key_name="dev-9c-efs-bastion",
        sg_id="sg-0463165610158c82e",
    ),
    "internal": ResourceDict(
        vpc_id="vpc-08ee9f2dbd1c97ac6",  # Internal VPC
        key_name="9c_internal_tunnel",
        sg_id="",
    ),
    "mainnet": ResourceDict(
        vpc_id="vpc-01a0ef2aa2c41bb26",  # Main VPC
        key_name="9c_main_bastion",
        sg_id="",
    ),
}


class SharedStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        config: Config = kwargs.pop("config")
        self.resource_data = RESOURCE_DICT.get(config.stage, None)
        if self.resource_data is None:
            raise KeyError(f"{config.stage} is not valid stage. Please select one of {list(RESOURCE_DICT.keys())}")
        super().__init__(scope, construct_id, **kwargs)

        # VPC
        self.vpc = _ec2.Vpc.from_lookup(self, f"{config.stage}-9c-season_pass-vpc", vpc_id=self.resource_data.vpc_id)

        # SQS
        self.unload_dlq = _sqs.Queue(self, f"{config.stage}-9c-season_pass-unload-dlq")
        self.unload_q = _sqs.Queue(
            self, f"{config.stage}-9c-season_pass-unload-queue",
            dead_letter_queue=_sqs.DeadLetterQueue(max_receive_count=2, queue=self.unload_dlq),
            visibility_timeout=cdk_core.Duration.seconds(120),
        )
        self.brave_dlq = _sqs.Queue(self, f"{config.stage}-9c-season_pass-brave-dlq")
        self.brave_q = _sqs.Queue(
            self, f"{config.stage}-9c-season_pass-brave-queue",
            dead_letter_queue=_sqs.DeadLetterQueue(max_receive_count=2, queue=self.brave_dlq),
            visibility_timeout=cdk_core.Duration.seconds(120),
        )

        # EC2 SG
        self.ec2_sg = _ec2.SecurityGroup(
            self, f"{config.stage}-9c-season_pass-ec2-sg", vpc=self.vpc, allow_all_outbound=True
        )
        self.ec2_sg.add_ingress_rule(
            peer=_ec2.Peer.ipv4("0.0.0.0/0"),
            connection=_ec2.Port.tcp(22),
            description="Allow SSH from outside",
        )
        self.ec2_sg.add_ingress_rule(
            peer=self.ec2_sg,
            connection=_ec2.Port.tcp(22),
            description="Allow SSH from outside",
        )

        # RDS
        self.rds_security_group = _ec2.SecurityGroup(
            self, f"{config.stage}-9c-season_pass-rds-sg", vpc=self.vpc, allow_all_outbound=True
        )
        self.rds_security_group.add_ingress_rule(
            peer=_ec2.Peer.ipv4("0.0.0.0/0"),
            connection=_ec2.Port.tcp(5432),
            description="Allow PSQL from outside",
        )
        self.rds_security_group.add_ingress_rule(
            peer=self.rds_security_group,
            connection=_ec2.Port.tcp(5432),
            description="Allow PSQL from outside",
        )
        self.credentials = _rds.Credentials.from_username("season_pass")
        if config.stage == "mainnet":
            self.rds = _rds.DatabaseCluster(
                self, f"{config.stage}-9c-season_pass-aurora-cluster",
                cluster_identifier=f"{config.stage}-9c-season-pass-aurora-cluster",
                engine=_rds.DatabaseClusterEngine.aurora_postgres(version=_rds.AuroraPostgresEngineVersion.VER_15_2),
                default_database_name="season_pass",
                credentials=self.credentials,
                vpc=self.vpc, vpc_subnets=_ec2.SubnetSelection(),
                security_groups=[self.rds_security_group],
                instance_update_behaviour=_rds.InstanceUpdateBehaviour.ROLLING,
                deletion_protection=True,
                storage_type=_rds.DBClusterStorageType.AURORA,
            )
            self.rds_endpoint = self.rds.cluster_endpoint.socket_address
        else:
            self.rds = _rds.DatabaseInstance(
                self, f"{config.stage}-9c-season_pass-rds",
                instance_identifier=f"{config.stage}-9c-season-pass-rds",
                engine=_rds.DatabaseInstanceEngine.postgres(version=_rds.PostgresEngineVersion.VER_15_2),
                vpc=self.vpc,
                vpc_subnets=_ec2.SubnetSelection(),
                database_name="season_pass",
                credentials=self.credentials,
                instance_type=_ec2.InstanceType.of(_ec2.InstanceClass.BURSTABLE4_GRAVITON, _ec2.InstanceSize.MICRO),
                security_groups=[self.rds_security_group],
            )
            self.rds_endpoint = self.rds.db_instance_endpoint_address

        # SecureStrings in Parameter Store
        PARAMETER_LIST = (
            ("KMS_KEY_ID", True),
            ("JWT_TOKEN_SECRET", True)
        )
        ssm = boto3.client("ssm", region_name=config.region_name,
                           aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
                           aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY")
                           )

        param_value_dict = {}
        for param, secure in PARAMETER_LIST:
            param_value_dict[param] = None
            try:
                prev_param = fetch_parameter(config.region_name, f"{config.stage}_9c_SEASON_PASS_{param}", secure)
                logging.debug(prev_param["Value"])
                if prev_param["Value"] != getattr(config, param.lower()):
                    logging.info(f"The value of {param} has been changed. Update to new value...")
                    raise ValueError("Update to new value")
                else:
                    param_value_dict[param] = prev_param
                    logging.info(f"{param} has already been set.")
            except (ssm.exceptions.ParameterNotFound, ValueError):
                try:
                    ssm.put_parameter(
                        Name=f"{config.stage}_9c_SEASON_PASS_{param}",
                        Value=getattr(config, param.lower()),
                        Type="SecureString" if secure else "String",
                        Overwrite=True
                    )
                    logging.info(f"{config.stage}_9c_SEASON_PASS_{param} has been set")
                    param_value_dict[param] = fetch_parameter(
                        config.region_name, f"{config.stage}_9c_SEASON_PASS_{param}", secure
                    )
                except Exception as e:
                    logging.error(e)
                    raise e

        for k, v in param_value_dict.items():
            setattr(self, f"{k.lower()}_arn", v["ARN"])
