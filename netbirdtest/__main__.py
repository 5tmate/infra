import base64
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
letsencrypt_email = config.get("letsencrypt_email") or f"admin@{zone_name}"
litestream_version = config.get("litestream_version") or "0.5.17"
litestream_bucket = config.require("litestream_bucket")
root_volume_size = config.get_int("root_volume_size") or 30
instance_types = config.get_object("instance_types") or ["t3.small", "t3a.small", "t2.small"]
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

user_data = pulumi.Output.all(eip.id, eip.public_ip).apply(
    lambda a: (
        _user_data.replace("__DOMAIN__", domain)
        .replace("__BUCKET__", litestream_bucket)
        .replace("__REGION__", region)
        .replace("__LE_EMAIL__", letsencrypt_email)
        .replace("__LITESTREAM_VERSION__", litestream_version)
        .replace("__EIP_ALLOC__", a[0])
        .replace("__EIP_ADDR__", a[1])
        .replace("__STANDBY_NAME__", f"{NAME}-standby")
    )
)


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
    policy=pulumi.Output.all(asg.arn, alerts.arn, region).apply(
        lambda a: json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Action": "autoscaling:DescribeAutoScalingGroups",
                        "Resource": "*",
                    },
                    {
                        "Effect": "Allow",
                        "Action": "autoscaling:SetDesiredCapacity",
                        "Resource": a[0],
                    },
                    {
                        "Effect": "Allow",
                        "Action": "ec2:DescribeInstances",
                        "Resource": "*",
                    },
                    {
                        "Effect": "Allow",
                        "Action": ["ec2:StartInstances", "ec2:StopInstances"],
                        "Resource": "*",
                        "Condition": {"StringEquals": {"ec2:ResourceTag/Name": f"{NAME}-standby"}},
                    },
                    {
                        "Effect": "Allow",
                        "Action": "ssm:SendCommand",
                        "Resource": f"arn:aws:ssm:{a[2]}::document/AWS-RunShellScript",
                    },
                    {
                        "Effect": "Allow",
                        "Action": "ssm:SendCommand",
                        "Resource": "arn:aws:ec2:*:*:instance/*",
                        "Condition": {"StringEquals": {"ssm:resourceTag/App": "5tmate"}},
                    },
                    {
                        "Effect": "Allow",
                        "Action": "ssm:GetCommandInvocation",
                        "Resource": "*",
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
            "ASG_NAME": NAME,
            "STANDBY_NAME": f"{NAME}-standby",
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
