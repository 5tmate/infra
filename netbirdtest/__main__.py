import json
from pathlib import Path

import pulumi
import pulumi_aws as aws

VPC_CIDR = "10.2.0.0/24"
SUBNET_CIDR = "10.2.0.0/28"
AZ = "ap-northeast-1a"
NAME = "5tmate-netbirdtest"
AMI_PARAMETER = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"

tags = {"App": "5tmate", "ManagedBy": "pulumi"}

config = pulumi.Config()
zone_name = config.require("zone_name")
allowed_ssh_cidr = config.require("allowed_ssh_cidr")
ssh_public_key = config.require("ssh_public_key")
hostname = config.get("hostname") or "netbirdtest"
instance_type = config.get("instance_type") or "t3.small"
root_volume_size = config.get_int("root_volume_size") or 30

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
        {
            "description": "SSH from operator IP",
            "protocol": "tcp",
            "from_port": 22,
            "to_port": 22,
            "cidr_blocks": [allowed_ssh_cidr],
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

instance_profile = aws.iam.InstanceProfile(
    "instance-profile",
    role=ssm_role.name,
    tags={**tags, "Name": NAME},
)


litestream_bucket = aws.s3.BucketV2(
    "litestream",
    bucket="5tmate-netbirdtest-litestream",
    force_destroy=True,
    tags={**tags, "Name": NAME},
)

aws.s3.BucketPublicAccessBlock(
    "litestream-pab",
    bucket=litestream_bucket.id,
    block_public_acls=True,
    block_public_policy=True,
    ignore_public_acls=True,
    restrict_public_buckets=True,
)

aws.iam.RolePolicy(
    "litestream-s3",
    role=ssm_role.name,
    policy=litestream_bucket.arn.apply(
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
                        "Action": "s3:ListBucket",
                        "Resource": arn,
                    },
                ],
            }
        )
    ),
)

key_pair = aws.ec2.KeyPair(
    "key",
    public_key=ssh_public_key,
    tags={**tags, "Name": NAME},
)

ami = aws.ssm.get_parameter(name=AMI_PARAMETER).value

user_data = (Path(__file__).parent / "files" / "user_data.sh").read_text()

instance = aws.ec2.Instance(
    "netbirdtest",
    ami=ami,
    instance_type=instance_type,
    subnet_id=subnet.id,
    vpc_security_group_ids=[sg.id],
    iam_instance_profile=instance_profile.name,
    key_name=key_pair.key_name,
    associate_public_ip_address=True,
    metadata_options={
        "http_endpoint": "enabled",
        "http_tokens": "required",
    },
    root_block_device={
        "volume_type": "gp3",
        "volume_size": root_volume_size,
        "encrypted": True,
        "delete_on_termination": True,
    },
    user_data=user_data,
    tags={**tags, "Name": NAME},
    opts=pulumi.ResourceOptions(ignore_changes=["ami"], depends_on=[litestream_bucket]),
)


eip = aws.ec2.Eip(
    "eip",
    domain="vpc",
    tags={**tags, "Name": NAME},
)

aws.ec2.EipAssociation(
    "eip-assoc",
    instance_id=instance.id,
    allocation_id=eip.id,
)


zone = aws.route53.get_zone(name=zone_name, private_zone=False)

aws.route53.Record(
    "a",
    zone_id=zone.zone_id,
    name=domain,
    type="A",
    ttl=60,
    records=[eip.public_ip],
)

aws.route53.Record(
    "wildcard",
    zone_id=zone.zone_id,
    name=f"*.{domain}",
    type="CNAME",
    ttl=60,
    records=[domain],
)


pulumi.export("instance_id", instance.id)
pulumi.export("public_ip", eip.public_ip)
pulumi.export("domain", domain)
pulumi.export("dashboard_url", f"https://{domain}")
pulumi.export("litestream_bucket", litestream_bucket.bucket)
pulumi.export("vpc_id", vpc.id)
pulumi.export("security_group_id", sg.id)
pulumi.export("ssh", eip.public_ip.apply(lambda ip: f"ssh ec2-user@{ip}"))
pulumi.export(
    "ssm",
    instance.id.apply(lambda i: f"aws ssm start-session --target {i} --region ap-northeast-1"),
)
