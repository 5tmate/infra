import base64
from collections.abc import Sequence
from pathlib import Path

import pulumi
import pulumi_aws as aws

import policies

USER_DATA_TEMPLATE = (Path(__file__).parent / "user_data.sh").read_text()


def render_user_data(
    *,
    role: str,
    bucket: pulumi.Input[str],
    region: str,
    cluster_name: str,
    service_name: str,
    nb_dir: str,
    images: Sequence[str],
) -> pulumi.Output[str]:
    return pulumi.Output.from_input(bucket).apply(
        lambda b: (
            USER_DATA_TEMPLATE.replace("__BUCKET__", b)
            .replace("__REGION__", region)
            .replace("__CLUSTER__", cluster_name)
            .replace("__SERVICE__", service_name)
            .replace("__ROLE__", role)
            .replace("__NB_DIR__", nb_dir)
            .replace("__IMAGES__", " ".join(images))
        )
    )


class SpotHost(pulumi.ComponentResource):
    def __init__(
        self,
        name: str,
        *,
        resource_name: str,
        cluster: aws.ecs.Cluster,
        subnet_id: pulumi.Input[str],
        security_group_ids: Sequence[pulumi.Input[str]],
        ami: str,
        instance_types: Sequence[str],
        desired_capacity: int,
        user_data: pulumi.Input[str],
        state_bucket_arn: pulumi.Input[str],
        propagated_tags: dict[str, str],
        opts: pulumi.ResourceOptions | None = None,
    ):
        super().__init__("netbird:ec2:SpotHost", name, None, opts)
        child = pulumi.ResourceOptions(parent=self)
        tags = {"Name": resource_name}

        role = aws.iam.Role(
            "instance-role",
            assume_role_policy=policies.assume_role("ec2.amazonaws.com"),
            tags=tags,
            opts=child,
        )
        aws.iam.RolePolicyAttachment(
            "instance-ssm",
            role=role.name,
            policy_arn="arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore",
            opts=child,
        )
        aws.iam.RolePolicyAttachment(
            "instance-ecs",
            role=role.name,
            policy_arn="arn:aws:iam::aws:policy/service-role/AmazonEC2ContainerServiceforEC2Role",
            opts=child,
        )
        self.instance_profile = aws.iam.InstanceProfile(
            "instance-profile", role=role.name, tags=tags, opts=child
        )
        self.backup_access = aws.iam.RolePolicy(
            "instance-backup-access",
            role=role.name,
            policy=pulumi.Output.from_input(state_bucket_arn).apply(policies.bucket_access),
            opts=child,
        )

        service_update = aws.iam.RolePolicy(
            "instance-service-update",
            role=role.name,
            policy=cluster.arn.apply(
                lambda arn: policies.service_update(
                    f"{arn.replace(':cluster/', ':service/')}/{resource_name}"
                )
            ),
            opts=child,
        )

        launch_template = aws.ec2.LaunchTemplate(
            "launch-template",
            name_prefix=f"{resource_name}-",
            image_id=ami,
            vpc_security_group_ids=list(security_group_ids),
            iam_instance_profile={"arn": self.instance_profile.arn},
            metadata_options={"http_endpoint": "enabled", "http_tokens": "required"},
            block_device_mappings=[
                {
                    "device_name": "/dev/xvda",
                    "ebs": {
                        "volume_type": "gp3",
                        "encrypted": "true",
                        "delete_on_termination": "true",
                    },
                }
            ],
            user_data=pulumi.Output.from_input(user_data).apply(
                lambda s: base64.b64encode(s.encode()).decode()
            ),
            update_default_version=True,
            tag_specifications=[
                {"resource_type": "instance", "tags": tags},
                {"resource_type": "volume", "tags": tags},
            ],
            tags=tags,
            opts=pulumi.ResourceOptions(
                parent=self, depends_on=[self.backup_access, service_update]
            ),
        )

        self.asg = aws.autoscaling.Group(
            "asg",
            name=resource_name,
            vpc_zone_identifiers=[subnet_id],
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
                    "overrides": [{"instance_type": t} for t in instance_types],
                },
            },
            tags=[
                {"key": k, "value": v, "propagate_at_launch": True}
                for k, v in propagated_tags.items()
            ],
            opts=pulumi.ResourceOptions(parent=self, depends_on=[cluster]),
        )

        capacity_provider = aws.ecs.CapacityProvider(
            "capacity-provider",
            name=resource_name,
            auto_scaling_group_provider={
                "auto_scaling_group_arn": self.asg.arn,
                "managed_termination_protection": "DISABLED",
                "managed_scaling": {"status": "DISABLED"},
            },
            tags=tags,
            opts=pulumi.ResourceOptions(parent=self, delete_before_replace=True),
        )
        aws.ecs.ClusterCapacityProviders(
            "cluster-capacity",
            cluster_name=cluster.name,
            capacity_providers=[capacity_provider.name],
            default_capacity_provider_strategies=[
                {"capacity_provider": capacity_provider.name, "weight": 1}
            ],
            opts=child,
        )

        self.asg_name = self.asg.name
        self.register_outputs({"asg_name": self.asg_name})
