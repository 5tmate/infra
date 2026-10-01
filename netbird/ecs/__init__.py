from collections.abc import Sequence

import pulumi
import pulumi_aws as aws

import policies

from .containers import container_definitions


class NetBirdService(pulumi.ComponentResource):
    def __init__(
        self,
        name: str,
        *,
        resource_name: str,
        cluster: aws.ecs.Cluster,
        log_group: pulumi.Input[str],
        state_bucket_arn: pulumi.Input[str],
        zone_id: str,
        certificate_domains: Sequence[str],
        region: str,
        nb_dir: str,
        management_url: str,
        idp_url: str,
        config_s3_uri: pulumi.Input[str],
        config_sha256: pulumi.Input[str],
        litestream_image: str,
        dashboard_image: str,
        server_image: str,
        aws_cli_image: str,
        start_after: Sequence[pulumi.Resource],
        opts: pulumi.ResourceOptions | None = None,
    ):
        super().__init__("netbird:ecs:NetBirdService", name, None, opts)
        child = pulumi.ResourceOptions(parent=self)
        tags = {"Name": resource_name}

        execution_role = aws.iam.Role(
            "ecs-execution",
            name=f"{resource_name}-ecs-execution",
            assume_role_policy=policies.assume_role("ecs-tasks.amazonaws.com"),
            tags=tags,
            opts=child,
        )
        aws.iam.RolePolicyAttachment(
            "ecs-execution-managed",
            role=execution_role.name,
            policy_arn="arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy",
            opts=child,
        )

        task_role = aws.iam.Role(
            "ecs-task",
            name=f"{resource_name}-ecs-task",
            assume_role_policy=policies.assume_role("ecs-tasks.amazonaws.com"),
            tags=tags,
            opts=child,
        )
        aws.iam.RolePolicy(
            "task-backup-access",
            role=task_role.name,
            policy=pulumi.Output.from_input(state_bucket_arn).apply(policies.bucket_access),
            opts=child,
        )
        aws.iam.RolePolicy(
            "task-dns01",
            role=task_role.name,
            policy=policies.dns01_challenge(zone_id, certificate_domains),
            opts=child,
        )

        task_definition = aws.ecs.TaskDefinition(
            "task-definition",
            family=resource_name,
            network_mode="bridge",
            requires_compatibilities=["EC2"],
            execution_role_arn=execution_role.arn,
            task_role_arn=task_role.arn,
            runtime_platform={"cpu_architecture": "ARM64", "operating_system_family": "LINUX"},
            volumes=[
                {"name": "netbird-data", "host_path": f"{nb_dir}/data"},
                {"name": "netbird-config"},
                {"name": "litestream-config", "host_path": f"{nb_dir}/litestream.yml"},
            ],
            container_definitions=pulumi.Output.json_dumps(
                container_definitions(
                    log_group=log_group,
                    region=region,
                    management_url=management_url,
                    idp_url=idp_url,
                    config_s3_uri=config_s3_uri,
                    config_sha256=config_sha256,
                    litestream_image=litestream_image,
                    dashboard_image=dashboard_image,
                    server_image=server_image,
                    aws_cli_image=aws_cli_image,
                )
            ),
            tags=tags,
            opts=child,
        )

        self.service = aws.ecs.Service(
            "service",
            name=resource_name,
            cluster=cluster.arn,
            task_definition=task_definition.arn,
            desired_count=1,
            deployment_minimum_healthy_percent=0,
            deployment_maximum_percent=100,
            launch_type="EC2",
            wait_for_steady_state=True,
            tags=tags,
            opts=pulumi.ResourceOptions(parent=self, depends_on=list(start_after)),
        )

        self.service_name = self.service.name
        self.register_outputs({"service_name": self.service_name})
