import pulumi
import pulumi_aws as aws

from cloudfront import ALL_METHODS, READ_METHODS, Cdn
from ec2 import SpotHost, render_user_data
from ecs import NetBirdService
from failover import Failover
from s3 import NetBirdConfig, PrivateBucket
from vpc import Network

NAME = pulumi.get_project()
STANDBY_NAME = f"{NAME}-standby"
BACKUP_BUCKET = f"{NAME}-backup"
NB_DIR = "/opt/netbird"
INSTANCE_TYPES = ["t4g.small", "t4g.medium", "m6g.medium"]
LITESTREAM_IMAGE = "litestream/litestream:0.5.17"
DASHBOARD_IMAGE = "netbirdio/dashboard:v2.93.0"
SERVER_IMAGE = "netbirdio/netbird-server:0.79.0-rc.1"
AWS_CLI_IMAGE = "public.ecr.aws/aws-cli/aws-cli:2.37.1"
AMI_PARAMETER = "/aws/service/ecs/optimized-ami/amazon-linux-2023/arm64/recommended/image_id"

config = pulumi.Config()
zone_name = config.require("zone_name")  # Route53 zone
hostname = config.get("hostname", "netbird")  # 管理網域主機名
management_domain = f"{hostname}.{zone_name}"  # 直連指 EIP，否則指 443 的 CloudFront
dashboard_domain = f"admin-{hostname}.{zone_name}"  # 指 80 的 CloudFront
relay_domain = f"relay-{hostname}.{zone_name}"  # 一律指 EIP，STUN、relay、signal 共用
desired_capacity = config.get_int("desired_capacity", 1)  # 主機數
origin_tls = config.get_bool("origin_tls", True)  # 443 是 TLS 還是明文
peers_via_cloudfront = config.get_bool("peers_via_cloudfront", False)  # API 跟登入走 CloudFront
default_tags = pulumi.Config("aws").require_object("defaultTags")

if not (origin_tls or peers_via_cloudfront):
    raise ValueError("origin_tls=false requires peers_via_cloudfront=true")

management_url = f"https://{management_domain}"  # peer、dashboard、登入都連這裡
relay_url = f"{'https' if origin_tls else 'http'}://{relay_domain}:443"  # server 對外宣告的位址
idp_url = f"{management_url}/oauth2"  # 登入的 Dex
health_url = f"{idp_url}/.well-known/openid-configuration"  # Lambda 探活

zone = aws.route53.get_zone(name=zone_name, private_zone=False)
region = aws.get_region().name
ami = aws.ssm.get_parameter(name=AMI_PARAMETER).value
cloudfront_origins = aws.ec2.get_managed_prefix_list(
    name="com.amazonaws.global.cloudfront.origin-facing"
)
us_east_1 = aws.Provider("us-east-1", region="us-east-1", default_tags=default_tags)
wildcard_certificate = aws.acm.get_certificate(
    domain=f"*.{zone_name}",
    statuses=["ISSUED"],
    most_recent=True,
    opts=pulumi.InvokeOptions(provider=us_east_1),
)

network = Network(
    "network",
    resource_name=NAME,
    vpc_cidr="10.2.0.0/24",
    subnet_cidr="10.2.0.0/28",
    standby_subnet_cidr="10.2.0.16/28",
    az="ap-northeast-1a",
    standby_az="ap-northeast-1c",
)


def bucket_exists(name: str) -> bool:
    try:
        aws.s3.get_bucket(bucket=name)
    except Exception:
        return False
    return True


backup = PrivateBucket(
    "backup",
    bucket=BACKUP_BUCKET,
    adopt=bucket_exists(BACKUP_BUCKET),
    tags={"Name": BACKUP_BUCKET, "Purpose": "litestream-backup"},
)

server_config = NetBirdConfig(
    "config",
    bucket=backup.bucket.id,
    key="config/config.yaml",
    management_domain=management_domain,
    relay_domain=relay_domain,
    dashboard_domain=dashboard_domain,
    exposed_address=relay_url,
    issuer=idp_url,
    letsencrypt_enabled=origin_tls,
    auth_secret=config.require_secret("auth_secret"),
    session_key=config.require_secret("session_key"),
    store_encryption_key=config.require_secret("store_encryption_key"),
    owner_email=config.require_secret("owner_email"),
    owner_password_hash=config.require_secret("owner_password_hash"),
    acme_email=config.require_secret("acme_email"),
)

only_cloudfront = {"prefix_list_ids": [cloudfront_origins.id]}
anyone = {"cidr_blocks": ["0.0.0.0/0"]}
egress_all = [
    {
        "description": "all outbound (SSM, image pull, Route53 for ACME, peers)",
        "protocol": "-1",
        "from_port": 0,
        "to_port": 0,
        **anyone,
    }
]

# 分兩個 SG，prefix list 一次算 55 條
dashboard_sg = aws.ec2.SecurityGroup(
    "dashboard-ingress",
    vpc_id=network.vpc_id,
    description="dashboard, only from cloudfront",
    ingress=[
        {
            "description": "dashboard, only from cloudfront",
            "protocol": "tcp",
            "from_port": 80,
            "to_port": 80,
            **only_cloudfront,
        }
    ],
    egress=egress_all,
    tags={"Name": f"{NAME}-dashboard"},
)

sg = aws.ec2.SecurityGroup(
    "control-plane",
    vpc_id=network.vpc_id,
    description="netbird self-hosted control plane",
    ingress=[
        {
            "description": "management gRPC, API, embedded IdP, signal and relay",
            "protocol": "tcp",
            "from_port": 443,
            "to_port": 443,
            **anyone,
        },
        {
            "description": "embedded STUN",
            "protocol": "udp",
            "from_port": 3478,
            "to_port": 3478,
            **anyone,
        },
    ],
    egress=egress_all,
    tags={"Name": NAME},
)

eip = aws.ec2.Eip("eip", domain="vpc", tags={"Name": NAME})
cluster = aws.ecs.Cluster("cluster", name=NAME, tags={"Name": NAME})
ecs_logs = aws.cloudwatch.LogGroup(
    "ecs-logs", name=f"/ecs/{NAME}", retention_in_days=14, tags={"Name": NAME}
)


def user_data(role: str) -> pulumi.Output[str]:
    return render_user_data(
        role=role,
        bucket=backup.name,
        region=region,
        cluster_name=NAME,
        litestream_image=LITESTREAM_IMAGE,
        nb_dir=NB_DIR,
    )


host = SpotHost(
    "host",
    resource_name=NAME,
    cluster=cluster,
    subnet_id=network.subnet_id,
    security_group_ids=[sg.id, dashboard_sg.id],
    ami=ami,
    instance_types=INSTANCE_TYPES,
    desired_capacity=desired_capacity,
    user_data=user_data("primary"),
    state_bucket_arn=backup.arn,
    propagated_tags={**default_tags["tags"], "Name": NAME, "AmazonECSManaged": ""},
)

failover = Failover(
    "failover",
    resource_name=NAME,
    cluster=cluster,
    asg_name=host.asg_name,
    eip_id=eip.id,
    standby_name=STANDBY_NAME,
    management_domain=management_domain,
    health_url=health_url,
)

netbird = NetBirdService(
    "netbird",
    resource_name=NAME,
    cluster=cluster,
    log_group=ecs_logs.name,
    state_bucket_arn=backup.arn,
    zone_id=zone.zone_id,
    certificate_domains=[management_domain, relay_domain],
    region=region,
    nb_dir=NB_DIR,
    management_url=management_url,
    idp_url=idp_url,
    config_s3_uri=server_config.s3_uri,
    config_sha256=server_config.content_sha256,
    litestream_image=LITESTREAM_IMAGE,
    dashboard_image=DASHBOARD_IMAGE,
    server_image=SERVER_IMAGE,
    aws_cli_image=AWS_CLI_IMAGE,
    start_after=[failover.task_events_target, failover.task_events_invoke],
)

standby = aws.ec2.Instance(
    "standby",
    ami=ami,
    instance_type="t4g.small",
    subnet_id=network.standby_subnet_id,
    vpc_security_group_ids=[sg.id, dashboard_sg.id],
    iam_instance_profile=host.instance_profile.name,
    user_data=user_data("standby"),
    user_data_replace_on_change=True,
    metadata_options={"http_endpoint": "enabled", "http_tokens": "required"},
    root_block_device={"volume_type": "gp3", "encrypted": True, "delete_on_termination": True},
    tags={"Name": STANDBY_NAME},
    opts=pulumi.ResourceOptions(depends_on=[host.backup_access, netbird.service]),
)

dashboard = Cdn(
    "dashboard",
    resource_name=NAME,
    us_east_1_provider=us_east_1,
    certificate_arn=wildcard_certificate.arn,
    alias=dashboard_domain,
    origin_domain=eip.public_dns,
    origin_protocol_policy="http-only",
    origin_port=80,
    origin_ssl_protocols=["TLSv1.2"],
    allowed_methods=READ_METHODS,
    response_headers_policy=None,
    grpc_enabled=False,
)

management = Cdn(
    "management",
    resource_name=f"{NAME}-management",
    us_east_1_provider=us_east_1,
    certificate_arn=wildcard_certificate.arn,
    alias=management_domain,
    origin_domain=eip.public_dns,
    origin_protocol_policy="https-only" if origin_tls else "http-only",
    origin_port=443,
    origin_ssl_protocols=["SSLv3", "TLSv1", "TLSv1.1", "TLSv1.2"],
    allowed_methods=ALL_METHODS,
    response_headers_policy="Managed-CORS-With-Preflight",
    grpc_enabled=origin_tls,
)


def cloudfront_alias(cdn: Cdn) -> list[dict]:
    return [
        {"name": cdn.domain_name, "zone_id": cdn.hosted_zone_id, "evaluate_target_health": False}
    ]


aws.route53.Record(
    "dashboard-record",
    zone_id=zone.zone_id,
    name=dashboard_domain,
    type="A",
    aliases=cloudfront_alias(dashboard),
)

aws.route53.Record(
    "relay-record",
    zone_id=zone.zone_id,
    name=relay_domain,
    type="A",
    ttl=60,
    records=[eip.public_ip],
)

aws.route53.Record(
    "management-record",
    zone_id=zone.zone_id,
    name=management_domain,
    type="A",
    ttl=None if peers_via_cloudfront else 60,
    records=None if peers_via_cloudfront else [eip.public_ip],
    aliases=cloudfront_alias(management) if peers_via_cloudfront else None,
)

pulumi.export("asg_name", host.asg_name)
pulumi.export("public_ip", eip.public_ip)
pulumi.export("management_domain", management_domain)
pulumi.export("dashboard_url", f"https://{dashboard_domain}")
pulumi.export("dashboard_cloudfront_domain", dashboard.domain_name)
pulumi.export("management_cloudfront_domain", management.domain_name)
pulumi.export("litestream_bucket", backup.name)
pulumi.export("vpc_id", network.vpc_id)
pulumi.export("security_group_id", sg.id)
pulumi.export("dashboard_security_group_id", dashboard_sg.id)
pulumi.export("alerts_topic_arn", failover.topic_arn)
pulumi.export("failover_function", failover.function_name)
pulumi.export("asg_event_log_group", failover.event_log_group)
