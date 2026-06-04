import hashlib
import pathlib

import pulumi
import pulumi_aws as aws
import pulumi_command as command
import pulumi_docker_build as docker_build



region = aws.get_region().name
account_id = aws.get_caller_identity().account_id

tags = {"App": "5tmate", "Stack": "nuclei", "ManagedBy": "pulumi"}

CONTAINER_NAME = "nuclei"
ADAPTER_LAYER = (
    f"arn:aws:lambda:{region}:753240598075:layer:LambdaAdapterLayerX86:25"
)

# VPC default lookup

default_vpcs = aws.ec2.get_vpcs(filters=[{"name": "is-default", "values": ["true"]}])
vpc_id = default_vpcs.ids[0]
subnet_lookup = aws.ec2.get_subnets(
    filters=[{"name": "vpc-id", "values": [vpc_id]}],
)
default_sg = aws.ec2.get_security_group(vpc_id=vpc_id, name="default")

# ECR for Fargate

ecr_repo = aws.ecr.Repository(
    "nuclei",
    name="nuclei",
    image_tag_mutability="MUTABLE",
    force_delete=True,
    tags=tags,
)

# Docker build src/fargate and push to ECR

ecr_auth = aws.ecr.get_authorization_token_output(registry_id=ecr_repo.registry_id)

nuclei_image = docker_build.Image(
    "nuclei",
    tags=[ecr_repo.repository_url.apply(lambda u: f"{u}:latest")],
    context=docker_build.BuildContextArgs(location="src/fargate"),
    platforms=["linux/amd64"],
    push=True,
    registries=[
        docker_build.RegistryArgs(
            address=ecr_repo.repository_url,
            username=ecr_auth.user_name,
            password=ecr_auth.password,
        )
    ],
)

# CloudWatch log

ecs_logs = aws.cloudwatch.LogGroup(
    "ecs-nuclei",
    name="/ecs/nuclei",
    retention_in_days=30,
    tags=tags,
)

# ECS cluster

cluster = aws.ecs.Cluster("nuclei", name="nuclei", tags=tags)

aws.ecs.ClusterCapacityProviders(
    "nuclei-capacity",
    cluster_name=cluster.name,
    capacity_providers=["FARGATE", "FARGATE_SPOT"],
)

# ECS task roles

ecs_tasks_assume = pulumi.Output.json_dumps(
    {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "ecs-tasks.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }
        ],
    }
)

task_exec_role = aws.iam.Role(
    "task-exec",
    name="nuclei-task-exec",
    assume_role_policy=ecs_tasks_assume,
    tags=tags,
)

aws.iam.RolePolicyAttachment(
    "task-exec-managed",
    role=task_exec_role.name,
    policy_arn="arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy",
)

task_role = aws.iam.Role(
    "task",
    name="nuclei-task",
    assume_role_policy=ecs_tasks_assume,
    tags=tags,
)

# ECS task definition

container_defs = pulumi.Output.json_dumps(
    [
        {
            "name": CONTAINER_NAME,
            "image": nuclei_image.ref,
            "essential": True,
            "stopTimeout": 120,
            "logConfiguration": {
                "logDriver": "awslogs",
                "options": {
                    "awslogs-group": ecs_logs.name,
                    "awslogs-region": region,
                    "awslogs-stream-prefix": "nuclei",
                },
            },
        }
    ]
)

task_def = aws.ecs.TaskDefinition(
    "nuclei",
    family="nuclei",
    requires_compatibilities=["FARGATE"],
    network_mode="awsvpc",
    cpu="1024",
    memory="2048",
    execution_role_arn=task_exec_role.arn,
    task_role_arn=task_role.arn,
    container_definitions=container_defs,
    tags=tags,
)

# Lambda zip build (compile bootstrap and zip) 

lambda_src_root = pathlib.Path(__file__).parent / "src" / "lambda"
_h = hashlib.sha256()
for _f in sorted(lambda_src_root.rglob("*")):
    if not _f.is_file():
        continue
    _rel = _f.relative_to(lambda_src_root).as_posix()
    if _rel.startswith("dist/"):
        continue
    _h.update(_rel.encode())
    _h.update(b"\x00")
    _h.update(_f.read_bytes())
lambda_src_hash = _h.hexdigest()

lambda_build = command.local.Command(
    "lambda-build",
    create=(
        "cd src/lambda && "
        "mkdir -p dist && "
        "GOOS=linux GOARCH=amd64 CGO_ENABLED=0 "
        "go build -o dist/bootstrap . && "
        "(cd dist && zip -q lambda.zip bootstrap)"
    ),
    triggers=[lambda_src_hash],
)

# Lambda IAM role + policies

lambda_role = aws.iam.Role(
    "lambda-starter",
    name="nuclei-lambda-starter",
    assume_role_policy=pulumi.Output.json_dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"Service": "lambda.amazonaws.com"},
                    "Action": "sts:AssumeRole",
                }
            ],
        }
    ),
    tags=tags,
)

aws.iam.RolePolicyAttachment(
    "lambda-logs",
    role=lambda_role.name,
    policy_arn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
)

task_def_family_arn = pulumi.Output.format(
    "arn:aws:ecs:{0}:{1}:task-definition/{2}:*",
    region,
    account_id,
    task_def.family,
)

aws.iam.RolePolicy(
    "lambda-ecs",
    role=lambda_role.id,
    policy=pulumi.Output.json_dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": "ecs:RunTask",
                    "Resource": task_def_family_arn,
                },
                {
                    "Effect": "Allow",
                    "Action": "iam:PassRole",
                    "Resource": [task_role.arn, task_exec_role.arn],
                },
            ],
        }
    ),
)

# Lambda function

fn = aws.lambda_.Function(
    "starter",
    name="nuclei-starter",
    role=lambda_role.arn,
    runtime="provided.al2023",
    handler="bootstrap",
    architectures=["x86_64"],
    code=pulumi.FileArchive("src/lambda/dist/lambda.zip"),
    timeout=10,
    memory_size=128,
    layers=[ADAPTER_LAYER],
    environment={
        "variables": {
            "CLUSTER_ARN": cluster.arn,
            "TASK_DEFINITION_ARN": task_def.arn,
            "CONTAINER_NAME": CONTAINER_NAME,
            "SUBNETS": ",".join(subnet_lookup.ids),
            "SECURITY_GROUPS": default_sg.id,
            "PORT": "8080",
            "AWS_LAMBDA_EXEC_WRAPPER": "/opt/bootstrap",
        }
    },
    opts=pulumi.ResourceOptions(depends_on=[lambda_build]),
    tags=tags,
)

# Function URL + invoke permission

fn_url = aws.lambda_.FunctionUrl(
    "starter-url",
    function_name=fn.name,
    authorization_type="AWS_IAM",
)

aws.lambda_.Permission(
    "url-invoke",
    function=fn.name,
    action="lambda:InvokeFunctionUrl",
    principal=account_id,
    function_url_auth_type="AWS_IAM",
)

# Exports

pulumi.export("function_url", fn_url.function_url)
pulumi.export("cluster_arn", cluster.arn)
pulumi.export("task_definition_arn", task_def.arn)
pulumi.export("ecr_repo_url", ecr_repo.repository_url)




