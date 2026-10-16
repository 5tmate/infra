import pulumi
import pulumi_aws as aws

import policies

from .containers import relays_container_definitions


class RelaysService(pulumi.ComponentResource):
    def __init__(
        self,
        name: str,
        *,
        resource_name: str,
        cluster: aws.ecs.Cluster,
        log_group: pulumi.Input[str],
        region: str,
        zone_id: str,
        certs_image: str,
        signal_image: str,
        relay_image: str,
        signal_domain: str,
        relay_domain: str,
        auth_secret: pulumi.Input[str],
        acme_email: pulumi.Input[str],
        nb_dir: str,
        opts: pulumi.ResourceOptions | None = None,
    ):
        super().__init__("netbird:ecs:RelaysService", name, None, opts)
        child = pulumi.ResourceOptions(parent=self)
        tags = {"Name": resource_name}

        secret = aws.ssm.Parameter(
            "relays-auth-secret",
            name=f"/{resource_name}/relay-auth-secret",
            type="SecureString",
            value=auth_secret,
            tags=tags,
            opts=child,
        )

        execution_role = aws.iam.Role(
            "relays-ecs-execution",
            name=f"{resource_name}-ecs-execution",
            assume_role_policy=policies.assume_role("ecs-tasks.amazonaws.com"),
            tags=tags,
            opts=child,
        )
        aws.iam.RolePolicyAttachment(
            "relays-ecs-execution-managed",
            role=execution_role.name,
            policy_arn="arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy",
            opts=child,
        )
        aws.iam.RolePolicy(
            "relays-ecs-execution-secret",
            role=execution_role.name,
            policy=secret.arn.apply(policies.read_secret),
            opts=child,
        )

        task_role = aws.iam.Role(
            "relays-ecs-task",
            name=f"{resource_name}-ecs-task",
            assume_role_policy=policies.assume_role("ecs-tasks.amazonaws.com"),
            tags=tags,
            opts=child,
        )
        aws.iam.RolePolicy(
            "relays-task-dns01",
            role=task_role.name,
            policy=policies.dns01_challenge(zone_id, [signal_domain, relay_domain]),
            opts=child,
        )

        task_definition = aws.ecs.TaskDefinition(
            "relays-task-definition",
            family=resource_name,
            network_mode="bridge",
            requires_compatibilities=["EC2"],
            execution_role_arn=execution_role.arn,
            task_role_arn=task_role.arn,
            runtime_platform={"cpu_architecture": "ARM64", "operating_system_family": "LINUX"},
            volumes=[{"name": "certs", "host_path": f"{nb_dir}/certs"}],
            container_definitions=pulumi.Output.json_dumps(
                relays_container_definitions(
                    log_group=log_group,
                    region=region,
                    certs_image=certs_image,
                    signal_image=signal_image,
                    relay_image=relay_image,
                    signal_domain=signal_domain,
                    relay_domain=relay_domain,
                    auth_secret_arn=secret.arn,
                    acme_email=acme_email,
                )
            ),
            tags=tags,
            opts=child,
        )

        self.service = aws.ecs.Service(
            "relays-service",
            name=resource_name,
            cluster=cluster.arn,
            task_definition=task_definition.arn,
            desired_count=1,
            deployment_minimum_healthy_percent=0,
            deployment_maximum_percent=100,
            launch_type="EC2",
            tags=tags,
            opts=child,
        )

        self.service_name = self.service.name
        self.register_outputs({"service_name": self.service_name})
