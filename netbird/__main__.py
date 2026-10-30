import pulumi
import pulumi_aws as aws
import pulumi_random as random

from cloudfront import ALL_METHODS, READ_METHODS, Cdn
from ec2 import RelaysHost, SpotHost, render_user_data
from ecs import NetBirdService, RelaysService
from ecs.containers import SKIP_HOLD_FILE
from failover import Failover
from s3 import NetBirdConfig, PrivateBucket
from vpc import Network

NAME = pulumi.get_project()
STANDBY_NAME = f"{NAME}-standby"
NB_DIR = "/opt/netbird"
INSTANCE_TYPES = ["t4g.small", "t4g.medium", "m6g.medium"]
LITESTREAM_IMAGE = "litestream/litestream:0.5.17"
DASHBOARD_IMAGE = "netbirdio/dashboard:v2.96.0"
SERVER_IMAGE = "netbirdio/netbird-server:0.82.0"
SIGNAL_IMAGE = "netbirdio/signal:0.82.0"
RELAY_IMAGE = "netbirdio/relay:0.82.0"
CERTS_IMAGE = "certbot/dns-route53:v2.11.0"
AWS_CLI_IMAGE = "public.ecr.aws/aws-cli/aws-cli:2.37.1"
AMI_PARAMETER = "/aws/service/ecs/optimized-ami/amazon-linux-2023/arm64/recommended/image_id"
PEER_IP_METHODS = ["Login", "Sync", "SyncMeta"]  # NetBird 只在這幾個 gRPC 讀 peer 的 IP

config = pulumi.Config()
zone_name = config.require("zone_name")  # Route53 zone
hostname = config.get("hostname", "netbird")  # 管理網域主機名
management_domain = f"{hostname}.{zone_name}"  # 指 443 的 CloudFront
dashboard_domain = f"admin-{hostname}.{zone_name}"  # 指 80 的 CloudFront
origin_domain = f"origin-{hostname}.{zone_name}"  # 指 EIP，CloudFront 回源用，Let's Encrypt 簽這個
relays_domain = f"relays-{hostname}.{zone_name}"  # 指 relays EIP，signal + relay + stun 直連
desired_capacity = config.get_int("desired_capacity", 1)  # 主機數
relays_instance_type = config.get("relays_instance_type", "t4g.nano")  # relays 機型
self_stun = config.get_bool("self_stun", True)  # 自架 STUN(relays box)開關
public_stuns = list(config.get_object("public_stuns") or [])  # 額外公開 STUN uri 清單
login_geo_allow = list(config.get_object("login_geo_allow") or [])  # 允許登入的國別,空=不裝 WAF
default_tags = pulumi.Config("aws").require_object("defaultTags")


def s3_secret(key: str, make) -> pulumi.Output[str]:
    s3_key = f"secrets/{key}"
    try:
        body = aws.s3.get_object(bucket=BACKUP_BUCKET, key=s3_key).body
    except Exception as exc:
        if not any(m in str(exc) for m in ("NoSuchKey", "NoSuchBucket", "NotFound", "404")):
            raise
        value = make()
        aws.s3.BucketObject(
            f"secret-{key.replace('_', '-')}",
            bucket=backup.bucket.id,
            key=s3_key,
            content=value,
            content_type="text/plain",
            opts=pulumi.ResourceOptions(retain_on_delete=True),
        )
        return pulumi.Output.secret(value)
    return pulumi.Output.secret(body.strip())


def random_b64(name: str) -> pulumi.Output[str]:
    return random.RandomBytes(name, length=32).base64


def new_owner_hash() -> pulumi.Output[str]:
    password = random.RandomPassword("owner-password", length=20, special=False)
    pulumi.export("owner_password", password.result)
    return password.bcrypt_hash


management_url = f"https://{management_domain}"  # peer、dashboard、登入都連這裡
exposed_address = f"{management_url}:443"  # management 對外位址，走 CloudFront
signal_uri = f"https://{relays_domain}:443"  # signal 直連 relays box
relay_uri = f"rels://{relays_domain}:8443"  # relay 直連 relays box
stun_uris = (
    [f"stun:{relays_domain}:3478"] if self_stun else []
) + public_stuns  # 自架(self_stun 控制)與公開 STUN 併成一份清單,無主備之分
idp_url = f"{management_url}/oauth2"  # 登入的 Dex
health_url = f"{idp_url}/.well-known/openid-configuration"  # Lambda 探活

zone = aws.route53.get_zone(name=zone_name, private_zone=False)
region = aws.get_region().name
BACKUP_BUCKET = f"{region}-{NAME}-backup"
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
    second_subnet_cidr="10.2.0.32/28",
    standby_subnet_cidr="10.2.0.16/28",
    az="ap-northeast-1a",
    second_az="ap-northeast-1d",
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
    tags={"Name": BACKUP_BUCKET},
)

owner_email = config.get("owner_email") or f"admin@{zone_name}"
owner_password_hash = s3_secret("owner_password_hash", new_owner_hash)

auth_secret = s3_secret("auth_secret", lambda: random_b64("auth-secret"))
acme_email = pulumi.Output.secret(
    aws.ssm.get_parameter(name="/5tmate/netbird/acme_email", with_decryption=True).value
)

server_config = NetBirdConfig(
    "config",
    bucket=backup.bucket.id,
    key="config/config.yaml",
    origin_domain=origin_domain,
    dashboard_domain=dashboard_domain,
    exposed_address=exposed_address,
    signal_uri=signal_uri,
    relay_uri=relay_uri,
    stun_uris=stun_uris,
    issuer=idp_url,
    trusted_peers=sorted(entry.cidr for entry in cloudfront_origins.entries),
    auth_secret=auth_secret,
    session_key=s3_secret("session_key", lambda: random_b64("session-key")),
    store_encryption_key=s3_secret(
        "store_encryption_key", lambda: random_b64("store-encryption-key")
    ),
    owner_email=owner_email,
    owner_password_hash=owner_password_hash,
    acme_email=acme_email,
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
            "description": "management, signal, relay and login, only from cloudfront",
            "protocol": "tcp",
            "from_port": 443,
            "to_port": 443,
            **only_cloudfront,
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
        service_name=NAME,
        nb_dir=NB_DIR,
        images=[LITESTREAM_IMAGE, DASHBOARD_IMAGE, SERVER_IMAGE, AWS_CLI_IMAGE],
    )


host = SpotHost(
    "host",
    resource_name=NAME,
    cluster=cluster,
    subnet_ids=network.host_subnet_ids,
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
    skip_hold_path=f"{NB_DIR}/data/{SKIP_HOLD_FILE}",
)

netbird = NetBirdService(
    "netbird",
    resource_name=NAME,
    cluster=cluster,
    log_group=ecs_logs.name,
    state_bucket_arn=backup.arn,
    zone_id=zone.zone_id,
    certificate_domains=[origin_domain],
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

relays_eip = aws.ec2.Eip("relays-eip", domain="vpc", tags={"Name": f"{NAME}-relays"})
relays_sg = aws.ec2.SecurityGroup(
    "relays-ingress",
    vpc_id=network.vpc_id,
    description="netbird signal, relay and stun, direct",
    ingress=[
        {
            "description": "signal gRPC",
            "protocol": "tcp",
            "from_port": 443,
            "to_port": 443,
            **anyone,
        },
        {
            "description": "relay ws",
            "protocol": "tcp",
            "from_port": 8443,
            "to_port": 8443,
            **anyone,
        },
        {
            "description": "relay quic",
            "protocol": "udp",
            "from_port": 8443,
            "to_port": 8443,
            **anyone,
        },
        *(
            [
                {
                    "description": "STUN",
                    "protocol": "udp",
                    "from_port": 3478,
                    "to_port": 3478,
                    **anyone,
                }
            ]
            if self_stun
            else []
        ),
    ],
    egress=egress_all,
    tags={"Name": f"{NAME}-relays"},
)
relays_cluster = aws.ecs.Cluster(
    "relays-cluster", name=f"{NAME}-relays", tags={"Name": f"{NAME}-relays"}
)
relays_logs = aws.cloudwatch.LogGroup(
    "relays-logs",
    name=f"/ecs/{NAME}-relays",
    retention_in_days=14,
    tags={"Name": f"{NAME}-relays"},
)

relays_host = RelaysHost(
    "relays-host",
    resource_name=f"{NAME}-relays",
    cluster=relays_cluster,
    subnet_ids=[*network.host_subnet_ids, network.standby_subnet_id],
    security_group_ids=[relays_sg.id],
    ami=ami,
    instance_type=relays_instance_type,
    eip_allocation_id=relays_eip.id,
    region=region,
)

relays_service = RelaysService(
    "relays-service",
    resource_name=f"{NAME}-relays",
    cluster=relays_cluster,
    log_group=relays_logs.name,
    region=region,
    certs_image=CERTS_IMAGE,
    signal_image=SIGNAL_IMAGE,
    relay_image=RELAY_IMAGE,
    domain=relays_domain,
    auth_secret=auth_secret,
    acme_email=acme_email,
    zone_id=zone.zone_id,
    nb_dir=NB_DIR,
    enable_stun=self_stun,
)

dashboard = Cdn(
    "dashboard",
    resource_name=NAME,
    us_east_1_provider=us_east_1,
    certificate_arn=wildcard_certificate.arn,
    alias=dashboard_domain,
    origin_domain=origin_domain,
    origin_protocol_policy="http-only",
    origin_port=80,
    origin_ssl_protocols=["TLSv1.2"],
    allowed_methods=READ_METHODS,
    origin_request_policy="Managed-AllViewer",
    response_headers_policy=None,
    grpc_enabled=False,
    forwarded_for_paths=[],
    geo_whitelist=login_geo_allow,
)

login_waf = None
if login_geo_allow:
    oidc_discovery_path = "/oauth2/.well-known/openid-configuration"
    login_waf = aws.wafv2.WebAcl(
        "login-geo",
        name=f"{NAME}-login-geo",
        scope="CLOUDFRONT",
        default_action={"allow": {}},
        rules=[
            {
                "name": "allow-oidc-discovery",
                "priority": 0,
                "action": {"allow": {}},
                "statement": {
                    "byte_match_statement": {
                        "search_string": oidc_discovery_path,
                        "field_to_match": {"uri_path": {}},
                        "positional_constraint": "EXACTLY",
                        "text_transformations": [
                            {"priority": 0, "type": "URL_DECODE"},
                            {"priority": 1, "type": "NORMALIZE_PATH"},
                            {"priority": 2, "type": "LOWERCASE"},
                        ],
                    }
                },
                "visibility_config": {
                    "sampled_requests_enabled": True,
                    "cloudwatch_metrics_enabled": True,
                    "metric_name": f"{NAME}-allow-discovery",
                },
            },
            {
                "name": "allow-taiwan",
                "priority": 1,
                "action": {"allow": {}},
                "statement": {"geo_match_statement": {"country_codes": login_geo_allow}},
                "visibility_config": {
                    "sampled_requests_enabled": True,
                    "cloudwatch_metrics_enabled": True,
                    "metric_name": f"{NAME}-allow-taiwan",
                },
            },
            {
                "name": "block-login",
                "priority": 2,
                "action": {"block": {}},
                "statement": {
                    "byte_match_statement": {
                        "search_string": "/oauth2",
                        "field_to_match": {"uri_path": {}},
                        "positional_constraint": "STARTS_WITH",
                        "text_transformations": [
                            {"priority": 0, "type": "URL_DECODE"},
                            {"priority": 1, "type": "NORMALIZE_PATH"},
                            {"priority": 2, "type": "LOWERCASE"},
                        ],
                    }
                },
                "visibility_config": {
                    "sampled_requests_enabled": True,
                    "cloudwatch_metrics_enabled": True,
                    "metric_name": f"{NAME}-block-login",
                },
            },
        ],
        visibility_config={
            "sampled_requests_enabled": True,
            "cloudwatch_metrics_enabled": True,
            "metric_name": f"{NAME}-login-geo",
        },
        tags={"Name": f"{NAME}-login-geo"},
        opts=pulumi.ResourceOptions(provider=us_east_1),
    )

management = Cdn(
    "management",
    resource_name=f"{NAME}-management",
    us_east_1_provider=us_east_1,
    certificate_arn=wildcard_certificate.arn,
    alias=management_domain,
    origin_domain=origin_domain,
    origin_protocol_policy="https-only",
    origin_port=443,
    origin_ssl_protocols=["SSLv3", "TLSv1", "TLSv1.1", "TLSv1.2"],
    allowed_methods=ALL_METHODS,
    origin_request_policy="Managed-AllViewerExceptHostHeader",
    response_headers_policy="Managed-CORS-With-Preflight",
    grpc_enabled=True,
    forwarded_for_paths=[f"/management.ManagementService/{m}" for m in PEER_IP_METHODS],
    web_acl_arn=(login_waf.arn if login_waf else None),
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
    "origin-record",
    zone_id=zone.zone_id,
    name=origin_domain,
    type="A",
    ttl=60,
    records=[eip.public_ip],
)

aws.route53.Record(
    "management-record",
    zone_id=zone.zone_id,
    name=management_domain,
    type="A",
    aliases=cloudfront_alias(management),
)

aws.route53.Record(
    "relays-record",
    zone_id=zone.zone_id,
    name=relays_domain,
    type="A",
    ttl=60,
    records=[relays_eip.public_ip],
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
pulumi.export("relays_public_ip", relays_eip.public_ip)
pulumi.export("relays_domain", relays_domain)
