import base64

import pulumi
import pulumi_aws as aws

from .network import sg, subnet
from .settings import (
    AMI_PARAMETER,
    BACKUP_BUCKET,
    LITESTREAM_IMAGE,
    NAME,
    NB_DIR,
    ROOT,
    ROOT_VOLUME_SIZE,
    desired_capacity,
    region,
    tags,
)
from .storage import backup_access, instance_profile, netbird_config

ami = aws.ssm.get_parameter(name=AMI_PARAMETER).value


eip = aws.ec2.Eip(
    "eip",
    domain="vpc",
    tags={**tags, "Name": NAME},
)


cluster = aws.ecs.Cluster("cluster", name=NAME, tags={**tags, "Name": NAME})


_user_data = (ROOT / "files" / "user_data.sh").read_text()


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
            "spot_allocation_strategy": "lowest-price",
            "spot_instance_pools": 3,
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
    opts=pulumi.ResourceOptions(depends_on=[cluster]),
)


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
