import json

import pulumi
import pulumi_aws as aws

from .containers import containers
from .failover import task_events_invoke, task_events_target
from .machine import ami, cluster, ecs_logs, eip, standby_user_data
from .network import sg, standby_subnet
from .settings import NAME, NB_DIR, ROOT_VOLUME_SIZE, STANDBY_NAME, domain, tags, zone_name
from .storage import backup_access, backup_bucket, instance_profile, netbird_config

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

zone = aws.route53.get_zone(name=zone_name, private_zone=False)

aws.iam.RolePolicy(
    "ecs-task-acme",
    role=task_role.name,
    policy=json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": ["route53:ListHostedZones", "route53:ListHostedZonesByName"],
                    "Resource": "*",
                },
                {
                    "Effect": "Allow",
                    "Action": "route53:GetChange",
                    "Resource": "arn:aws:route53:::change/*",
                },
                {
                    "Effect": "Allow",
                    "Action": "route53:ListResourceRecordSets",
                    "Resource": f"arn:aws:route53:::hostedzone/{zone.zone_id}",
                },
                {
                    "Effect": "Allow",
                    "Action": "route53:ChangeResourceRecordSets",
                    "Resource": f"arn:aws:route53:::hostedzone/{zone.zone_id}",
                    "Condition": {
                        "ForAllValues:StringEquals": {
                            "route53:ChangeResourceRecordSetsNormalizedRecordNames": [
                                f"_acme-challenge.{domain}"
                            ],
                            "route53:ChangeResourceRecordSetsRecordTypes": ["TXT"],
                        }
                    },
                },
            ],
        }
    ),
)


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
        {"name": "netbird-config", "host_path": f"{NB_DIR}/config.yaml"},
        {"name": "litestream-config", "host_path": f"{NB_DIR}/litestream.yml"},
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
    launch_type="EC2",
    wait_for_steady_state=True,
    tags={**tags, "Name": NAME},
    opts=pulumi.ResourceOptions(depends_on=[task_events_target, task_events_invoke]),
)


standby = aws.ec2.Instance(
    "standby",
    ami=ami,
    instance_type="t4g.small",
    subnet_id=standby_subnet.id,
    vpc_security_group_ids=[sg.id],
    iam_instance_profile=instance_profile.name,
    user_data=standby_user_data,
    user_data_replace_on_change=False,
    metadata_options={"http_endpoint": "enabled", "http_tokens": "required"},
    root_block_device={
        "volume_type": "gp3",
        "volume_size": ROOT_VOLUME_SIZE,
        "encrypted": True,
        "delete_on_termination": True,
    },
    tags={**tags, "Name": STANDBY_NAME},
    opts=pulumi.ResourceOptions(depends_on=[netbird_config, backup_access, service]),
)


aws.route53.Record(
    "a",
    zone_id=zone.zone_id,
    name=domain,
    type="A",
    ttl=60,
    records=[eip.public_ip],
)
