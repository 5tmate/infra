import json

import pulumi
import pulumi_aws as aws

from .machine import asg, cluster, eip
from .settings import NAME, ROOT, STANDBY_NAME, domain, tags

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
    evaluation_periods=20,
    datapoints_to_alarm=15,
    threshold=1,
    comparison_operator="LessThanThreshold",
    treat_missing_data="breaching",
    alarm_description="15 of the last 20 minutes had no running instance, fail over to the standby",
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
    evaluation_periods=6,
    datapoints_to_alarm=6,
    threshold=1,
    comparison_operator="GreaterThanOrEqualToThreshold",
    treat_missing_data="notBreaching",
    alarm_description=(
        "the group has had a running instance for 6 minutes, fail back to the primary"
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
                        ],
                        "Resource": a[0],
                    },
                    {
                        "Effect": "Allow",
                        "Action": "ecs:ListTasks",
                        "Resource": "*",
                        "Condition": {"ArnEquals": {"ecs:cluster": a[0]}},
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
    code=pulumi.FileArchive(str(ROOT / "lambda" / "src")),
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

task_events = aws.cloudwatch.EventRule(
    "task-events",
    name=f"{NAME}-task-events",
    description="a task of this service reaching RUNNING",
    event_pattern=cluster.arn.apply(
        lambda arn: json.dumps(
            {
                "source": ["aws.ecs"],
                "detail-type": ["ECS Task State Change"],
                "detail": {
                    "clusterArn": [arn],
                    "group": [f"service:{NAME}"],
                    "lastStatus": ["RUNNING"],
                },
            }
        )
    ),
    tags={**tags, "Name": NAME},
)

task_events_target = aws.cloudwatch.EventTarget(
    "task-events-lambda",
    rule=task_events.name,
    target_id="failover",
    arn=failover.arn,
)

task_events_invoke = aws.lambda_.Permission(
    "task-events-invoke",
    action="lambda:InvokeFunction",
    function=failover.name,
    principal="events.amazonaws.com",
    source_arn=task_events.arn,
)

aws.lambda_.Permission(
    "alarm-events-invoke",
    action="lambda:InvokeFunction",
    function=failover.name,
    principal="events.amazonaws.com",
    source_arn=alarm_events.arn,
)
