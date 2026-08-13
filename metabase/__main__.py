import json
from pathlib import Path

import pulumi
import pulumi_aws as aws

VPC_CIDR = "10.1.0.0/24"
SUBNET_CIDR = "10.1.0.0/28"
AZ = "ap-northeast-1a"

tags = {"App": "5tmate", "ManagedBy": "pulumi"}

config = pulumi.Config()
allowed_ssh_cidr = config.require("allowed_ssh_cidr")


vpc = aws.ec2.Vpc(
    "vpc",
    cidr_block=VPC_CIDR,
    enable_dns_support=True,
    enable_dns_hostnames=True,
    tags={**tags, "Name": "5tmate-metabase-honeypot"},
)


subnet = aws.ec2.Subnet(
    "subnet",
    vpc_id=vpc.id,
    cidr_block=SUBNET_CIDR,
    availability_zone=AZ,
    map_public_ip_on_launch=True,
    tags={**tags, "Name": "5tmate-metabase-honeypot"},
)


igw = aws.ec2.InternetGateway(
    "igw",
    vpc_id=vpc.id,
    tags={**tags, "Name": "5tmate-metabase-honeypot"},
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
    tags={**tags, "Name": "5tmate-metabase-honeypot"},
)

aws.ec2.RouteTableAssociation(
    "rt-assoc",
    subnet_id=subnet.id,
    route_table_id=route_table.id,
)


nuclei_sg = aws.ec2.SecurityGroup(
    "nuclei-sg",
    vpc_id=vpc.id,
    description="nuclei task",
    egress=[
        {
            "description": "all outbound (scan target + nuclei templates)",
            "protocol": "-1",
            "from_port": 0,
            "to_port": 0,
            "cidr_blocks": ["0.0.0.0/0"],
        }
    ],
    tags={**tags, "Name": "5tmate-nuclei-sg"},
)


ssh_sg = aws.ec2.SecurityGroup(
    "ssh-sg",
    vpc_id=vpc.id,
    description="metabase honeypot: SSH from operator IP only",
    ingress=[
        {
            "description": "SSH from operator IP",
            "protocol": "tcp",
            "from_port": 22,
            "to_port": 22,
            "cidr_blocks": [allowed_ssh_cidr],
        },
        {
            "description": "Metabase from nuclei SG",
            "protocol": "tcp",
            "from_port": 3000,
            "to_port": 3000,
            "security_groups": [nuclei_sg.id],
        },
    ],
    egress=[
        {
            "description": "all outbound (SSM, docker pull)",
            "protocol": "-1",
            "from_port": 0,
            "to_port": 0,
            "cidr_blocks": ["0.0.0.0/0"],
        }
    ],
    tags={**tags, "Name": "5tmate-metabase-honeypot"},
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
    tags={**tags, "Name": "5tmate-metabase-honeypot"},
)

aws.iam.RolePolicyAttachment(
    "ssm-core",
    role=ssm_role.name,
    policy_arn="arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore",
)

instance_profile = aws.iam.InstanceProfile(
    "instance-profile",
    role=ssm_role.name,
    tags={**tags, "Name": "5tmate-metabase-honeypot"},
)

ssh_public_key = config.require("ssh_public_key")

key_pair = aws.ec2.KeyPair(
    "key",
    public_key=ssh_public_key,
    tags={**tags, "Name": "5tmate-metabase-honeypot"},
)

ami = aws.ssm.get_parameter(
    name="/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64",
).value

metabase_tag = config.get("metabase_tag") or "v0.46.6"
_user_data = (Path(__file__).parent / "files" / "user_data.sh").read_text()
user_data = _user_data.replace("__METABASE_TAG__", metabase_tag)

instance = aws.ec2.Instance(
    "honeypot",
    ami=ami,
    instance_type="t3.small",
    subnet_id=subnet.id,
    vpc_security_group_ids=[ssh_sg.id],
    iam_instance_profile=instance_profile.name,
    key_name=key_pair.key_name,
    associate_public_ip_address=True,
    metadata_options={
        "http_endpoint": "enabled",
        "http_tokens": "required",
    },
    user_data=user_data,
    user_data_replace_on_change=True,
    tags={**tags, "Name": "5tmate-metabase-honeypot"},
)

pulumi.export("vpc_id", vpc.id)
pulumi.export("subnet_id", subnet.id)
pulumi.export("igw_id", igw.id)
pulumi.export("route_table_id", route_table.id)
pulumi.export("security_group_id", ssh_sg.id)
pulumi.export("nuclei_sg_id", nuclei_sg.id)
pulumi.export("public_ip", instance.public_ip)
pulumi.export("public_dns", instance.public_dns)
pulumi.export("instance_id", instance.id)
