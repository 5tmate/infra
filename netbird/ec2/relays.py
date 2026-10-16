import base64
from collections.abc import Sequence
from pathlib import Path

import pulumi
import pulumi_aws as aws

import policies

USER_DATA_TEMPLATE = (Path(__file__).parent / "relays.sh").read_text()


def _render_user_data(*, region, cluster_name, eip_allocation_id):
    return pulumi.Output.all(cluster_name, eip_allocation_id).apply(
        lambda v: (
            USER_DATA_TEMPLATE.replace("__REGION__", region)
            .replace("__CLUSTER__", v[0])
            .replace("__EIP_ALLOC__", v[1])
        )
    )


class RelaysHost(pulumi.ComponentResource):
    def __init__(
        self,
        name: str,
        *,
        resource_name: str,
        cluster: aws.ecs.Cluster,
        subnet_ids: Sequence[pulumi.Input[str]],
        security_group_ids: Sequence[pulumi.Input[str]],
        ami: str,
        instance_type: str,
        eip_allocation_id: pulumi.Input[str],
        region: str,
        opts: pulumi.ResourceOptions | None = None,
    ):
        super().__init__("netbird:ec2:RelaysHost", name, None, opts)
        child = pulumi.ResourceOptions(parent=self)
        tags = {"Name": resource_name}

        role = aws.iam.Role(
            "relays-instance-role",
            assume_role_policy=policies.assume_role("ec2.amazonaws.com"),
            tags=tags,
            opts=child,
        )
        aws.iam.RolePolicyAttachment(
            "relays-instance-ssm",
            role=role.name,
            policy_arn="arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore",
            opts=child,
        )
        aws.iam.RolePolicyAttachment(
            "relays-instance-ecs",
            role=role.name,
            policy_arn="arn:aws:iam::aws:policy/service-role/AmazonEC2ContainerServiceforEC2Role",
            opts=child,
        )
        aws.iam.RolePolicy(
            "relays-instance-eip",
            role=role.name,
            policy=policies.associate_address(),
            opts=child,
        )
        aws.iam.RolePolicy(
            "relays-instance-service-update",
            role=role.name,
            policy=cluster.arn.apply(
                lambda arn: policies.service_update(
                    f"{arn.replace(':cluster/', ':service/')}/{resource_name}"
                )
            ),
            opts=child,
        )
        instance_profile = aws.iam.InstanceProfile(
            "relays-instance-profile", role=role.name, tags=tags, opts=child
        )

        user_data = _render_user_data(
            region=region, cluster_name=cluster.name, eip_allocation_id=eip_allocation_id
        )

        launch_template = aws.ec2.LaunchTemplate(
            "relays-launch-template",
            name_prefix=f"{resource_name}-",
            image_id=ami,
            instance_type=instance_type,
            vpc_security_group_ids=list(security_group_ids),
            iam_instance_profile={"arn": instance_profile.arn},
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
            user_data=user_data.apply(lambda s: base64.b64encode(s.encode()).decode()),
            update_default_version=True,
            tag_specifications=[
                {"resource_type": "instance", "tags": tags},
                {"resource_type": "volume", "tags": tags},
            ],
            tags=tags,
            opts=child,
        )

        self.asg = aws.autoscaling.Group(
            "relays-asg",
            name=resource_name,
            vpc_zone_identifiers=list(subnet_ids),
            min_size=1,
            max_size=1,
            desired_capacity=1,
            health_check_type="EC2",
            health_check_grace_period=300,
            launch_template={"id": launch_template.id, "version": "$Latest"},
            tags=[{"key": "Name", "value": resource_name, "propagate_at_launch": True}],
            opts=pulumi.ResourceOptions(parent=self, depends_on=[cluster]),
        )

        self.asg_name = self.asg.name
        self.register_outputs({"asg_name": self.asg_name})
