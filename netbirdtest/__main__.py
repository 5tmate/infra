import base64
import json
from pathlib import Path

import pulumi
import pulumi_aws as aws

VPC_CIDR = "10.2.0.0/24"
SUBNET_CIDR = "10.2.0.0/28"
STANDBY_SUBNET_CIDR = "10.2.0.16/28"
AZ = "ap-northeast-1a"
STANDBY_AZ = "ap-northeast-1c"
NAME = "5tmate-netbirdtest"
STANDBY_NAME = f"{NAME}-standby"
NB_DIR = "/opt/netbird"
AMI_PARAMETER = "/aws/service/ecs/optimized-ami/amazon-linux-2023/arm64/recommended/image_id"

tags = {"App": "5tmate", "ManagedBy": "pulumi"}

config = pulumi.Config()
zone_name = config.require("zone_name")
allowed_ssh_cidr = config.require("allowed_ssh_cidr")
ssh_public_key = config.require("ssh_public_key")
hostname = config.get("hostname") or "netbirdtest"
letsencrypt_email = config.get("letsencrypt_email") or f"admin@{zone_name}"
litestream_version = config.get("litestream_version") or "0.5.17"
litestream_bucket = config.require("litestream_bucket")
root_volume_size = config.get_int("root_volume_size") or 30
instance_types = config.get_object("instance_types") or [
    "t4g.small",
    "t4g.medium",
    "m6g.medium",
]
standby_instance_type = config.get("standby_instance_type") or "t4g.small"
netbird_image = config.get("netbird_image") or "netbirdio/netbird-server:latest"
dashboard_image = config.get("dashboard_image") or "netbirdio/dashboard:latest"
traefik_image = config.get("traefik_image") or "traefik:v3.6"
litestream_image = config.get("litestream_image") or f"litestream/litestream:{litestream_version}"
on_demand_base = config.get_int("on_demand_base")
if on_demand_base is None:
    on_demand_base = 1
spot_max_price = config.get("spot_max_price")
desired_capacity = config.get_int("desired_capacity")
if desired_capacity is None:
    desired_capacity = 1
no_capacity_datapoints = config.get_int("no_capacity_datapoints") or 15
no_capacity_periods = no_capacity_datapoints + 5
capacity_stable_periods = config.get_int("capacity_stable_periods") or 6

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


backup_bucket = aws.s3.get_bucket(bucket=litestream_bucket)

aws.iam.RolePolicy(
    "litestream-s3",
    role=ssm_role.name,
    policy=json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
                    "Resource": f"{backup_bucket.arn}/*",
                },
                {
                    "Effect": "Allow",
                    "Action": ["s3:ListBucket", "s3:GetBucketLocation"],
                    "Resource": backup_bucket.arn,
                },
            ],
        }
    ),
)

key_pair = aws.ec2.KeyPair(
    "key",
    public_key=ssh_public_key,
    tags={**tags, "Name": NAME},
)

ami = aws.ssm.get_parameter(name=AMI_PARAMETER).value
region = aws.get_region().name


eip = aws.ec2.Eip(
    "eip",
    domain="vpc",
    tags={**tags, "Name": NAME},
)

aws.iam.RolePolicy(
    "eip-and-peer",
    role=ssm_role.name,
    policy=json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": [
                        "ec2:AssociateAddress",
                        "ec2:DescribeAddresses",
                        "ec2:DescribeInstances",
                    ],
                    "Resource": "*",
                }
            ],
        }
    ),
)


_user_data = (Path(__file__).parent / "files" / "user_data.sh").read_text()


def render_user_data(eip_allocation_id, role):
    return (
        _user_data.replace("__BUCKET__", litestream_bucket)
        .replace("__REGION__", region)
        .replace("__CLUSTER__", NAME)
        .replace("__EIP_ALLOC__", eip_allocation_id)
        .replace("__LITESTREAM_IMAGE__", litestream_image)
        .replace("__ROLE__", role)
        .replace("__NB_DIR__", NB_DIR)
        .replace("__STANDBY_NAME__", STANDBY_NAME)
    )


user_data = eip.id.apply(lambda i: render_user_data(i, "primary"))
standby_user_data = eip.id.apply(lambda i: render_user_data(i, "standby"))


launch_template = aws.ec2.LaunchTemplate(
    "lt",
    name_prefix=f"{NAME}-",
    image_id=ami,
    key_name=key_pair.key_name,
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
                "volume_size": root_volume_size,
                "encrypted": "true",
                "delete_on_termination": "true",
            },
        }
    ],
    user_data=user_data.apply(lambda t: base64.b64encode(t.encode()).decode()),
    update_default_version=True,
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
    capacity_rebalance=True,
    metrics_granularity="1Minute",
    enabled_metrics=["GroupInServiceInstances"],
    mixed_instances_policy={
        "instances_distribution": {
            "on_demand_base_capacity": on_demand_base,
            "on_demand_percentage_above_base_capacity": 0,
            "spot_allocation_strategy": "capacity-optimized",
            "spot_max_price": spot_max_price,
        },
        "launch_template": {
            "launch_template_specification": {
                "launch_template_id": launch_template.id,
                "version": "$Latest",
            },
            "overrides": [{"instance_type": t} for t in instance_types],
        },
    },
    tags=[
        {"key": k, "value": v, "propagate_at_launch": True}
        for k, v in {**tags, "Name": NAME}.items()
    ],
)


cluster = aws.ecs.Cluster("cluster", name=NAME, tags={**tags, "Name": NAME})

ecs_logs = aws.cloudwatch.LogGroup(
    "ecs-logs",
    name=f"/ecs/{NAME}",
    retention_in_days=14,
    tags={**tags, "Name": NAME},
)

capacity_provider = aws.ecs.CapacityProvider(
    "capacity",
    name=NAME,
    auto_scaling_group_provider={
        "auto_scaling_group_arn": asg.arn,
        "managed_termination_protection": "DISABLED",
        "managed_scaling": {"status": "DISABLED"},
    },
    tags={**tags, "Name": NAME},
)

aws.ecs.ClusterCapacityProviders(
    "cluster-capacity",
    cluster_name=cluster.name,
    capacity_providers=[capacity_provider.name],
    default_capacity_provider_strategies=[
        {"capacity_provider": capacity_provider.name, "weight": 1}
    ],
)

execution_role = aws.iam.Role(
    "ecs-execution",
    name=f"{NAME}-ecs-execution",
    assume_role_policy=json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": "sts:AssumeRole",
                    "Principal": {"Service": "ecs-tasks.amazonaws.com"},
                }
            ],
        }
    ),
    tags={**tags, "Name": NAME},
)

aws.iam.RolePolicyAttachment(
    "ecs-execution-managed",
    role=execution_role.name,
    policy_arn="arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy",
)

aws.iam.RolePolicy(
    "ecs-execution-envfile",
    role=execution_role.name,
    policy=json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": "s3:GetObject",
                    "Resource": f"{backup_bucket.arn}/config/*",
                },
                {
                    "Effect": "Allow",
                    "Action": "s3:GetBucketLocation",
                    "Resource": backup_bucket.arn,
                },
            ],
        }
    ),
)

task_role = aws.iam.Role(
    "ecs-task",
    name=f"{NAME}-ecs-task",
    assume_role_policy=json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": "sts:AssumeRole",
                    "Principal": {"Service": "ecs-tasks.amazonaws.com"},
                }
            ],
        }
    ),
    tags={**tags, "Name": NAME},
)

aws.iam.RolePolicy(
    "ecs-task-litestream",
    role=task_role.name,
    policy=json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
                    "Resource": f"{backup_bucket.arn}/*",
                },
                {
                    "Effect": "Allow",
                    "Action": ["s3:ListBucket", "s3:GetBucketLocation"],
                    "Resource": backup_bucket.arn,
                },
            ],
        }
    ),
)


def log_config(stream):
    return {
        "logDriver": "awslogs",
        "options": {
            "awslogs-group": f"/ecs/{NAME}",
            "awslogs-region": region,
            "awslogs-stream-prefix": stream,
        },
    }


grpc_paths = (
    "PathPrefix(`/signalexchange.SignalExchange/`) "
    "|| PathPrefix(`/management.ManagementService/`) "
    "|| PathPrefix(`/management.ProxyService/`)"
)
backend_paths = (
    "PathPrefix(`/relay`) || PathPrefix(`/ws-proxy/`) "
    "|| PathPrefix(`/api`) || PathPrefix(`/oauth2`)"
)

containers = [
    {
        "name": "traefik",
        "image": traefik_image,
        "essential": True,
        "memoryReservation": 128,
        "command": [
            "--log.level=INFO",
            "--accesslog=true",
            "--providers.docker=true",
            "--providers.docker.exposedbydefault=false",
            "--entrypoints.web.address=:80",
            "--entrypoints.websecure.address=:443",
            "--entrypoints.websecure.allowACMEByPass=true",
            "--entrypoints.websecure.transport.respondingTimeouts.readTimeout=0",
            "--entrypoints.websecure.transport.respondingTimeouts.writeTimeout=0",
            "--entrypoints.websecure.transport.respondingTimeouts.idleTimeout=0",
            "--entrypoints.web.http.redirections.entrypoint.to=websecure",
            "--entrypoints.web.http.redirections.entrypoint.scheme=https",
            f"--certificatesresolvers.letsencrypt.acme.email={letsencrypt_email}",
            "--certificatesresolvers.letsencrypt.acme.storage=/letsencrypt/acme.json",
            "--certificatesresolvers.letsencrypt.acme.tlschallenge=true",
            "--serverstransport.forwardingtimeouts.responseheadertimeout=0s",
            "--serverstransport.forwardingtimeouts.idleconntimeout=0s",
        ],
        "portMappings": [
            {"containerPort": 80, "hostPort": 80, "protocol": "tcp"},
            {"containerPort": 443, "hostPort": 443, "protocol": "tcp"},
        ],
        "mountPoints": [
            {"sourceVolume": "letsencrypt", "containerPath": "/letsencrypt"},
            {
                "sourceVolume": "docker-socket",
                "containerPath": "/var/run/docker.sock",
                "readOnly": True,
            },
        ],
        "logConfiguration": log_config("traefik"),
    },
    {
        "name": "dashboard",
        "image": dashboard_image,
        "essential": True,
        "memoryReservation": 128,
        "environmentFiles": [{"value": f"{backup_bucket.arn}/config/dashboard.env", "type": "s3"}],
        "dockerLabels": {
            "traefik.enable": "true",
            "traefik.http.routers.netbird-dashboard.rule": f"Host(`{domain}`)",
            "traefik.http.routers.netbird-dashboard.entrypoints": "websecure",
            "traefik.http.routers.netbird-dashboard.tls": "true",
            "traefik.http.routers.netbird-dashboard.tls.certresolver": "letsencrypt",
            "traefik.http.routers.netbird-dashboard.service": "dashboard",
            "traefik.http.routers.netbird-dashboard.priority": "1",
            "traefik.http.services.dashboard.loadbalancer.server.port": "80",
        },
        "logConfiguration": log_config("dashboard"),
    },
    {
        "name": "litestream",
        "image": litestream_image,
        "essential": True,
        "memoryReservation": 128,
        "command": ["replicate", "-config", "/etc/litestream.yml"],
        "stopTimeout": 60,
        "mountPoints": [
            {"sourceVolume": "netbird-data", "containerPath": "/var/lib/netbird"},
            {
                "sourceVolume": "litestream-config",
                "containerPath": "/etc/litestream.yml",
                "readOnly": True,
            },
        ],
        "logConfiguration": log_config("litestream"),
    },
    {
        "name": "netbird-server",
        "image": netbird_image,
        "essential": True,
        "memoryReservation": 768,
        "command": ["--config", "/etc/netbird/config.yaml"],
        "dependsOn": [{"containerName": "litestream", "condition": "START"}],
        "portMappings": [
            {"containerPort": 3478, "hostPort": 3478, "protocol": "udp"},
        ],
        "mountPoints": [
            {"sourceVolume": "netbird-data", "containerPath": "/var/lib/netbird"},
            {
                "sourceVolume": "netbird-config",
                "containerPath": "/etc/netbird/config.yaml",
                "readOnly": True,
            },
        ],
        "dockerLabels": {
            "traefik.enable": "true",
            "traefik.http.routers.netbird-grpc.rule": f"Host(`{domain}`) && ({grpc_paths})",
            "traefik.http.routers.netbird-grpc.entrypoints": "websecure",
            "traefik.http.routers.netbird-grpc.tls": "true",
            "traefik.http.routers.netbird-grpc.tls.certresolver": "letsencrypt",
            "traefik.http.routers.netbird-grpc.service": "netbird-server-h2c",
            "traefik.http.routers.netbird-grpc.priority": "100",
            "traefik.http.routers.netbird-backend.rule": f"Host(`{domain}`) && ({backend_paths})",
            "traefik.http.routers.netbird-backend.entrypoints": "websecure",
            "traefik.http.routers.netbird-backend.tls": "true",
            "traefik.http.routers.netbird-backend.tls.certresolver": "letsencrypt",
            "traefik.http.routers.netbird-backend.service": "netbird-server",
            "traefik.http.routers.netbird-backend.priority": "100",
            "traefik.http.services.netbird-server.loadbalancer.server.port": "80",
            "traefik.http.services.netbird-server-h2c.loadbalancer.server.port": "80",
            "traefik.http.services.netbird-server-h2c.loadbalancer.server.scheme": "h2c",
        },
        "logConfiguration": log_config("server"),
    },
]

task_definition = aws.ecs.TaskDefinition(
    "task",
    family=NAME,
    network_mode="bridge",
    requires_compatibilities=["EC2"],
    execution_role_arn=execution_role.arn,
    task_role_arn=task_role.arn,
    runtime_platform={"cpu_architecture": "ARM64", "operating_system_family": "LINUX"},
    volumes=[
        {"name": "netbird-data", "host_path": f"{NB_DIR}/data"},
        {"name": "letsencrypt", "host_path": f"{NB_DIR}/letsencrypt"},
        {"name": "netbird-config", "host_path": f"{NB_DIR}/config.yaml"},
        {"name": "litestream-config", "host_path": f"{NB_DIR}/litestream.yml"},
        {"name": "docker-socket", "host_path": "/var/run/docker.sock"},
    ],
    container_definitions=json.dumps(containers),
    tags={**tags, "Name": NAME},
    opts=pulumi.ResourceOptions(depends_on=[ecs_logs]),
)

service = aws.ecs.Service(
    "service",
    name=NAME,
    cluster=cluster.arn,
    task_definition=task_definition.arn,
    desired_count=1,
    deployment_minimum_healthy_percent=0,
    deployment_maximum_percent=100,
    capacity_provider_strategies=[{"capacity_provider": capacity_provider.name, "weight": 1}],
    tags={**tags, "Name": NAME},
)

standby = aws.ec2.Instance(
    "standby",
    ami=ami,
    instance_type=standby_instance_type,
    subnet_id=standby_subnet.id,
    vpc_security_group_ids=[sg.id],
    iam_instance_profile=instance_profile.name,
    key_name=key_pair.key_name,
    user_data=standby_user_data,
    user_data_replace_on_change=False,
    metadata_options={"http_endpoint": "enabled", "http_tokens": "required"},
    root_block_device={
        "volume_type": "gp3",
        "volume_size": root_volume_size,
        "encrypted": True,
        "delete_on_termination": True,
    },
    tags={**tags, "Name": STANDBY_NAME},
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


alerts = aws.sns.Topic(
    "alerts",
    name=f"{NAME}-alerts",
    tags={**tags, "Name": NAME},
)

aws.sns.TopicPolicy(
    "alerts-policy",
    arn=alerts.arn,
    policy=alerts.arn.apply(
        lambda arn: json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Principal": {"Service": "cloudwatch.amazonaws.com"},
                        "Action": "sns:Publish",
                        "Resource": arn,
                    }
                ],
            }
        )
    ),
)


no_capacity = aws.cloudwatch.MetricAlarm(
    "no-capacity",
    name=f"{NAME}-no-capacity",
    namespace="AWS/AutoScaling",
    metric_name="GroupInServiceInstances",
    dimensions={"AutoScalingGroupName": asg.name},
    statistic="Maximum",
    period=60,
    evaluation_periods=no_capacity_periods,
    datapoints_to_alarm=no_capacity_datapoints,
    threshold=1,
    comparison_operator="LessThanThreshold",
    treat_missing_data="breaching",
    alarm_description=(
        f"{no_capacity_datapoints} of the last {no_capacity_periods} minutes "
        "had no running instance, fail over to the standby"
    ),
    alarm_actions=[alerts.arn],
    tags={**tags, "Name": NAME},
)

capacity_stable = aws.cloudwatch.MetricAlarm(
    "capacity-stable",
    name=f"{NAME}-capacity-stable",
    namespace="AWS/AutoScaling",
    metric_name="GroupInServiceInstances",
    dimensions={"AutoScalingGroupName": asg.name},
    statistic="Maximum",
    period=60,
    evaluation_periods=capacity_stable_periods,
    datapoints_to_alarm=capacity_stable_periods,
    threshold=1,
    comparison_operator="GreaterThanOrEqualToThreshold",
    treat_missing_data="notBreaching",
    alarm_description=(
        f"the group has had a running instance for {capacity_stable_periods} "
        "consecutive minutes, fail back to the primary"
    ),
    tags={**tags, "Name": NAME},
)


event_log = aws.cloudwatch.LogGroup(
    "asg-events",
    name=f"/aws/events/{NAME}",
    retention_in_days=7,
    tags={**tags, "Name": NAME},
)

aws.cloudwatch.LogResourcePolicy(
    "asg-events-policy",
    policy_name=f"{NAME}-asg-events",
    policy_document=event_log.arn.apply(
        lambda arn: json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Principal": {
                            "Service": ["events.amazonaws.com", "delivery.logs.amazonaws.com"]
                        },
                        "Action": ["logs:CreateLogStream", "logs:PutLogEvents"],
                        "Resource": f"{arn}:*",
                    }
                ],
            }
        )
    ),
)

asg_events = aws.cloudwatch.EventRule(
    "asg-events",
    name=f"{NAME}-asg-events",
    description="every scaling event this group emits",
    event_pattern=json.dumps(
        {
            "source": ["aws.autoscaling"],
            "detail": {"AutoScalingGroupName": [NAME]},
        }
    ),
    tags={**tags, "Name": NAME},
)

aws.cloudwatch.EventTarget(
    "asg-events-log",
    rule=asg_events.name,
    target_id="log",
    arn=event_log.arn,
)

alarm_events = aws.cloudwatch.EventRule(
    "alarm-events",
    name=f"{NAME}-alarm-events",
    description="either failover alarm entering ALARM",
    event_pattern=pulumi.Output.all(no_capacity.name, capacity_stable.name).apply(
        lambda names: json.dumps(
            {
                "source": ["aws.cloudwatch"],
                "detail-type": ["CloudWatch Alarm State Change"],
                "detail": {
                    "alarmName": list(names),
                    "state": {"value": ["ALARM"]},
                },
            }
        )
    ),
    tags={**tags, "Name": NAME},
)

aws.cloudwatch.EventTarget(
    "alarm-events-log",
    rule=alarm_events.name,
    target_id="log",
    arn=event_log.arn,
)


failover_role = aws.iam.Role(
    "failover-lambda",
    name=f"{NAME}-failover-lambda",
    assume_role_policy=json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": "sts:AssumeRole",
                    "Principal": {"Service": "lambda.amazonaws.com"},
                }
            ],
        }
    ),
    tags={**tags, "Name": NAME},
)

aws.iam.RolePolicyAttachment(
    "failover-lambda-logs",
    role=failover_role.name,
    policy_arn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
)

aws.iam.RolePolicy(
    "failover-lambda-policy",
    role=failover_role.name,
    policy=pulumi.Output.all(cluster.arn, alerts.arn).apply(
        lambda a: json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Action": [
                            "ecs:ListContainerInstances",
                            "ecs:ListTasks",
                        ],
                        "Resource": a[0],
                    },
                    {
                        "Effect": "Allow",
                        "Action": [
                            "ecs:DescribeContainerInstances",
                            "ecs:DescribeTasks",
                            "ecs:UpdateContainerInstancesState",
                        ],
                        "Resource": "*",
                        "Condition": {"ArnEquals": {"ecs:cluster": a[0]}},
                    },
                    {
                        "Effect": "Allow",
                        "Action": ["ec2:DescribeInstances", "ec2:DescribeAddresses"],
                        "Resource": "*",
                    },
                    {
                        "Effect": "Allow",
                        "Action": "ec2:AssociateAddress",
                        "Resource": "*",
                    },
                    {
                        "Effect": "Allow",
                        "Action": ["ec2:StartInstances", "ec2:StopInstances"],
                        "Resource": "*",
                        "Condition": {"StringEquals": {"ec2:ResourceTag/Name": STANDBY_NAME}},
                    },
                    {"Effect": "Allow", "Action": "sns:Publish", "Resource": a[1]},
                ],
            }
        )
    ),
)

failover = aws.lambda_.Function(
    "failover",
    name=f"{NAME}-failover",
    role=failover_role.arn,
    runtime="python3.12",
    handler="handler.handler",
    code=pulumi.FileArchive(str(Path(__file__).parent / "lambda" / "src")),
    timeout=870,
    memory_size=256,
    reserved_concurrent_executions=1,
    environment={
        "variables": {
            "CLUSTER": NAME,
            "SERVICE": NAME,
            "STANDBY_NAME": STANDBY_NAME,
            "EIP_ALLOC": eip.id,
            "DOMAIN": domain,
            "HEALTH_PATH": "/oauth2",
            "NO_CAPACITY_ALARM": f"{NAME}-no-capacity",
            "CAPACITY_STABLE_ALARM": f"{NAME}-capacity-stable",
            "TOPIC_ARN": alerts.arn,
        }
    },
    tags={**tags, "Name": NAME},
)

aws.cloudwatch.EventTarget(
    "alarm-events-lambda",
    rule=alarm_events.name,
    target_id="failover",
    arn=failover.arn,
)

aws.lambda_.Permission(
    "alarm-events-invoke",
    action="lambda:InvokeFunction",
    function=failover.name,
    principal="events.amazonaws.com",
    source_arn=alarm_events.arn,
)


pulumi.export("asg_name", asg.name)
pulumi.export("public_ip", eip.public_ip)
pulumi.export("domain", domain)
pulumi.export("dashboard_url", f"https://{domain}")
pulumi.export("litestream_bucket", litestream_bucket)
pulumi.export("vpc_id", vpc.id)
pulumi.export("security_group_id", sg.id)
pulumi.export("alerts_topic_arn", alerts.arn)
pulumi.export("failover_function", failover.name)
pulumi.export("asg_event_log_group", event_log.name)
pulumi.export("ssh", eip.public_ip.apply(lambda ip: f"ssh ec2-user@{ip}"))
