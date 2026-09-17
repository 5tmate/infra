import base64
import json
from pathlib import Path

import pulumi
import pulumi_aws as aws
import pulumi_random as random

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


vpc = aws.ec2.Vpc(
    "vpc",
    cidr_block=VPC_CIDR,
    enable_dns_support=True,
    enable_dns_hostnames=True,
    tags={**tags, "Name": NAME},
)


subnet = aws.ec2.Subnet(
    "subnet",
    vpc_id=vpc.id,
    cidr_block=SUBNET_CIDR,
    availability_zone=AZ,
    map_public_ip_on_launch=True,
    tags={**tags, "Name": NAME},
)


standby_subnet = aws.ec2.Subnet(
    "standby-subnet",
    vpc_id=vpc.id,
    cidr_block=STANDBY_SUBNET_CIDR,
    availability_zone=STANDBY_AZ,
    map_public_ip_on_launch=True,
    tags={**tags, "Name": STANDBY_NAME},
)


igw = aws.ec2.InternetGateway(
    "igw",
    vpc_id=vpc.id,
    tags={**tags, "Name": NAME},
)


route_table = aws.ec2.RouteTable(
    "rt",
    vpc_id=vpc.id,
    routes=[
        {
            "cidr_block": "0.0.0.0/0",
            "gateway_id": igw.id,
        }
    ],
    tags={**tags, "Name": NAME},
)

aws.ec2.RouteTableAssociation(
    "rt-assoc",
    subnet_id=subnet.id,
    route_table_id=route_table.id,
)

aws.ec2.RouteTableAssociation(
    "standby-route",
    subnet_id=standby_subnet.id,
    route_table_id=route_table.id,
)


sg = aws.ec2.SecurityGroup(
    "control-plane",
    vpc_id=vpc.id,
    description="netbird self-hosted control plane",
    ingress=[
        {
            "description": "ACME http-01 challenge and redirect to HTTPS",
            "protocol": "tcp",
            "from_port": 80,
            "to_port": 80,
            "cidr_blocks": ["0.0.0.0/0"],
        },
        {
            "description": "dashboard, management API and gRPC, signal, relay",
            "protocol": "tcp",
            "from_port": 443,
            "to_port": 443,
            "cidr_blocks": ["0.0.0.0/0"],
        },
        {
            "description": "coturn STUN/TURN",
            "protocol": "udp",
            "from_port": 3478,
            "to_port": 3478,
            "cidr_blocks": ["0.0.0.0/0"],
        },
    ],
    egress=[
        {
            "description": "all outbound (SSM, image pull, ACME, peers)",
            "protocol": "-1",
            "from_port": 0,
            "to_port": 0,
            "cidr_blocks": ["0.0.0.0/0"],
        }
    ],
    tags={**tags, "Name": NAME},
)


ssm_role = aws.iam.Role(
    "ssm-role",
    assume_role_policy=json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": "sts:AssumeRole",
                    "Principal": {"Service": "ec2.amazonaws.com"},
                }
            ],
        }
    ),
    tags={**tags, "Name": NAME},
)

aws.iam.RolePolicyAttachment(
    "ssm-core",
    role=ssm_role.name,
    policy_arn="arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore",
)

aws.iam.RolePolicyAttachment(
    "ecs-container-instance",
    role=ssm_role.name,
    policy_arn="arn:aws:iam::aws:policy/service-role/AmazonEC2ContainerServiceforEC2Role",
)

instance_profile = aws.iam.InstanceProfile(
    "instance-profile",
    role=ssm_role.name,
    tags={**tags, "Name": NAME},
)


backup_bucket = aws.s3.Bucket(
    "backup",
    bucket=BACKUP_BUCKET,
    force_destroy=backup_force_destroy,
    tags={**tags, "Name": BACKUP_BUCKET, "Purpose": "litestream-backup"},
)

aws.s3.BucketPublicAccessBlock(
    "backup-private",
    bucket=backup_bucket.id,
    block_public_acls=True,
    block_public_policy=True,
    ignore_public_acls=True,
    restrict_public_buckets=True,
)

aws.s3.BucketServerSideEncryptionConfigurationV2(
    "backup-encryption",
    bucket=backup_bucket.id,
    rules=[{"apply_server_side_encryption_by_default": {"sse_algorithm": "AES256"}}],
)

auth_secret = random.RandomBytes(
    "auth-secret", length=32, opts=pulumi.ResourceOptions(protect=True)
)
session_key = random.RandomBytes(
    "session-key", length=32, opts=pulumi.ResourceOptions(protect=True)
)
store_key = random.RandomBytes(
    "store-encryption-key", length=32, opts=pulumi.ResourceOptions(protect=True)
)

_config_template = (Path(__file__).parent / "files" / "config.yaml").read_text()

netbird_config = aws.s3.BucketObject(
    "config-yaml",
    bucket=backup_bucket.id,
    key="config/config.yaml",
    content=pulumi.Output.all(
        auth_secret.base64, session_key.base64, store_key.base64
    ).apply(
        lambda v: (
            _config_template.replace("__DOMAIN__", domain)
            .replace("__AUTH_SECRET__", v[0])
            .replace("__SESSION_KEY__", v[1])
            .replace("__ENCRYPTION_KEY__", v[2])
        )
    ),
    opts=pulumi.ResourceOptions(ignore_changes=["content"], delete_before_replace=True),
)

backup_access = aws.iam.RolePolicy(
    "litestream-s3",
    role=ssm_role.name,
    policy=backup_bucket.arn.apply(
        lambda arn: json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
                        "Resource": f"{arn}/*",
                    },
                    {
                        "Effect": "Allow",
                        "Action": ["s3:ListBucket", "s3:GetBucketLocation"],
                        "Resource": arn,
                    },
                ],
            }
        )
    ),
)


ami = aws.ssm.get_parameter(name=AMI_PARAMETER).value
region = aws.get_region().name


eip = aws.ec2.Eip(
    "eip",
    domain="vpc",
    tags={**tags, "Name": NAME},
)


_user_data = (Path(__file__).parent / "files" / "user_data.sh").read_text()


def render_user_data(role):
    return (
        _user_data.replace("__BUCKET__", BACKUP_BUCKET)
        .replace("__REGION__", region)
        .replace("__CLUSTER__", NAME)
        .replace("__LITESTREAM_IMAGE__", LITESTREAM_IMAGE)
        .replace("__ROLE__", role)
        .replace("__NB_DIR__", NB_DIR)
    )


user_data = render_user_data("primary")
standby_user_data = render_user_data("standby")


launch_template = aws.ec2.LaunchTemplate(
    "lt",
    name_prefix=f"{NAME}-",
    image_id=ami,
    vpc_security_group_ids=[sg.id],
    iam_instance_profile={"arn": instance_profile.arn},
    metadata_options={
        "http_endpoint": "enabled",
        "http_tokens": "required",
    },
    block_device_mappings=[
        {
            "device_name": "/dev/xvda",
            "ebs": {
                "volume_type": "gp3",
                "volume_size": ROOT_VOLUME_SIZE,
                "encrypted": "true",
                "delete_on_termination": "true",
            },
        }
    ],
    user_data=base64.b64encode(user_data.encode()).decode(),
    update_default_version=True,
    opts=pulumi.ResourceOptions(depends_on=[netbird_config, backup_access]),
    tag_specifications=[
        {"resource_type": "instance", "tags": {**tags, "Name": NAME}},
        {"resource_type": "volume", "tags": {**tags, "Name": NAME}},
    ],
    tags={**tags, "Name": NAME},
)


asg = aws.autoscaling.Group(
    "asg",
    name=NAME,
    vpc_zone_identifiers=[subnet.id],
    min_size=0,
    max_size=1,
    desired_capacity=desired_capacity,
    health_check_type="EC2",
    health_check_grace_period=300,
    metrics_granularity="1Minute",
    enabled_metrics=["GroupInServiceInstances"],
    mixed_instances_policy={
        "instances_distribution": {
            "on_demand_base_capacity": 0,
            "on_demand_percentage_above_base_capacity": 0,
            "spot_allocation_strategy": "capacity-optimized",
        },
        "launch_template": {
            "launch_template_specification": {
                "launch_template_id": launch_template.id,
                "version": "$Latest",
            },
            "overrides": [{"instance_type": t} for t in ("t4g.small", "t4g.medium", "m6g.medium")],
        },
    },
    tags=[
        {"key": k, "value": v, "propagate_at_launch": True}
        for k, v in {**tags, "Name": NAME}.items()
    ],
)
