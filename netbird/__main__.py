import json

import pulumi
import pulumi_aws as aws

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


