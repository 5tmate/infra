from pathlib import Path

import pulumi
import pulumi_aws as aws

ROOT = Path(__file__).resolve().parent.parent

VPC_CIDR = "10.2.0.0/24"
SUBNET_CIDR = "10.2.0.0/28"
STANDBY_SUBNET_CIDR = "10.2.0.16/28"
AZ = "ap-northeast-1a"
STANDBY_AZ = "ap-northeast-1c"
NAME = "5tmate-netbird"
STANDBY_NAME = f"{NAME}-standby"
BACKUP_BUCKET = f"{NAME}-backup"
NB_DIR = "/opt/netbird"
ROOT_VOLUME_SIZE = 30
LITESTREAM_IMAGE = "litestream/litestream:0.5.17"
AMI_PARAMETER = "/aws/service/ecs/optimized-ami/amazon-linux-2023/arm64/recommended/image_id"

tags = {"App": "5tmate", "ManagedBy": "pulumi"}

config = pulumi.Config()
zone_name = config.require("zone_name")
hostname = config.get("hostname") or "netbird"
backup_force_destroy = config.get_bool("backup_force_destroy") or False
desired_capacity = config.get_int("desired_capacity")
if desired_capacity is None:
    desired_capacity = 1

domain = f"{hostname}.{zone_name}"


region = aws.get_region().name
