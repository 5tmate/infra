import pulumi_aws as aws

from .settings import (
    AZ,
    NAME,
    STANDBY_AZ,
    STANDBY_NAME,
    STANDBY_SUBNET_CIDR,
    SUBNET_CIDR,
    VPC_CIDR,
    tags,
)

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


cloudfront_origins = aws.ec2.get_managed_prefix_list(
    name="com.amazonaws.global.cloudfront.origin-facing"
)

sg = aws.ec2.SecurityGroup(
    "control-plane",
    vpc_id=vpc.id,
    description="netbird self-hosted control plane",
    ingress=[
        {
            "description": "dashboard, only from cloudfront",
            "protocol": "tcp",
            "from_port": 80,
            "to_port": 80,
            "prefix_list_ids": [cloudfront_origins.id],
        },
        {
            "description": "management gRPC, API, embedded IdP and relay",
            "protocol": "tcp",
            "from_port": 443,
            "to_port": 443,
            "cidr_blocks": ["0.0.0.0/0"],
        },
        {
            "description": "embedded STUN",
            "protocol": "udp",
            "from_port": 3478,
            "to_port": 3478,
            "cidr_blocks": ["0.0.0.0/0"],
        },
    ],
    egress=[
        {
            "description": "all outbound (SSM, image pull, Route53 for ACME, peers)",
            "protocol": "-1",
            "from_port": 0,
            "to_port": 0,
            "cidr_blocks": ["0.0.0.0/0"],
        }
    ],
    tags={**tags, "Name": NAME},
)
