import json
from pathlib import Path

import pulumi
import pulumi_aws as aws

import policies

LAMBDA_SOURCE = Path(__file__).parent / "lambda"


def _publish_policy(topic_arn: str) -> str:
    return json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"Service": "cloudwatch.amazonaws.com"},
                    "Action": "sns:Publish",
                    "Resource": topic_arn,
                }
            ],
        }
    )


def _log_delivery_policy(log_group_arn: str) -> str:
    return json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {
                        "Service": ["events.amazonaws.com", "delivery.logs.amazonaws.com"]
                    },
                    "Action": ["logs:CreateLogStream", "logs:PutLogEvents"],
                    "Resource": f"{log_group_arn}:*",
                }
            ],
        }
    )


def _lambda_policy(cluster_arn: str, topic_arn: str, standby_name: str) -> str:
    return json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": ["ecs:ListContainerInstances"],
                    "Resource": cluster_arn,
                },
                {
                    "Effect": "Allow",
                    "Action": "ecs:ListTasks",
                    "Resource": "*",
                    "Condition": {"ArnEquals": {"ecs:cluster": cluster_arn}},
                },
                {
                    "Effect": "Allow",
                    "Action": [
                        "ecs:DescribeContainerInstances",
                        "ecs:DescribeTasks",
                        "ecs:UpdateContainerInstancesState",
                    ],
                    "Resource": "*",
                    "Condition": {"ArnEquals": {"ecs:cluster": cluster_arn}},
                },
                {
                    "Effect": "Allow",
                    "Action": ["ec2:DescribeInstances", "ec2:DescribeAddresses"],
                    "Resource": "*",
                },
                {"Effect": "Allow", "Action": "ec2:AssociateAddress", "Resource": "*"},
                {
                    "Effect": "Allow",
                    "Action": ["ec2:StartInstances", "ec2:StopInstances"],
                    "Resource": "*",
                    "Condition": {"StringEquals": {"ec2:ResourceTag/Name": standby_name}},
                },
                {"Effect": "Allow", "Action": "sns:Publish", "Resource": topic_arn},
            ],
        }
    )


def _scaling_events_pattern(asg_name: str) -> str:
    return json.dumps(
        {"source": ["aws.autoscaling"], "detail": {"AutoScalingGroupName": [asg_name]}}
    )


def _alarm_events_pattern(alarm_names: list[str]) -> str:
    return json.dumps(
        {
            "source": ["aws.cloudwatch"],
            "detail-type": ["CloudWatch Alarm State Change"],
            "detail": {"alarmName": alarm_names, "state": {"value": ["ALARM"]}},
        }
    )


def _task_events_pattern(cluster_arn: str, service_name: str) -> str:
    return json.dumps(
        {
            "source": ["aws.ecs"],
            "detail-type": ["ECS Task State Change"],
            "detail": {
                "clusterArn": [cluster_arn],
                "group": [f"service:{service_name}"],
                "lastStatus": ["RUNNING"],
            },
        }
    )


class Failover(pulumi.ComponentResource):
    def __init__(
        self,
        name: str,
        *,
        resource_name: str,
        cluster: aws.ecs.Cluster,
        asg_name: pulumi.Input[str],
        eip_id: pulumi.Input[str],
        standby_name: str,
        management_domain: str,
        health_url: str,
        opts: pulumi.ResourceOptions | None = None,
    ):
        super().__init__("netbird:failover:Failover", name, None, opts)
        child = pulumi.ResourceOptions(parent=self)
        tags = {"Name": resource_name}

        self.alerts = aws.sns.Topic("alerts", name=f"{resource_name}-alerts", tags=tags, opts=child)
        aws.sns.TopicPolicy(
            "alerts-policy",
            arn=self.alerts.arn,
            policy=self.alerts.arn.apply(_publish_policy),
            opts=child,
        )

        no_capacity = aws.cloudwatch.MetricAlarm(
            "no-capacity",
            name=f"{resource_name}-no-capacity",
            namespace="AWS/AutoScaling",
            metric_name="GroupInServiceInstances",
            dimensions={"AutoScalingGroupName": asg_name},
            statistic="Maximum",
            period=60,
            evaluation_periods=20,
            datapoints_to_alarm=15,
            threshold=1,
            comparison_operator="LessThanThreshold",
            treat_missing_data="notBreaching",
            alarm_description=(
                "15 of the last 20 minutes had no running instance, fail over to the standby"
            ),
            alarm_actions=[self.alerts.arn],
            tags=tags,
            opts=child,
        )
        capacity_stable = aws.cloudwatch.MetricAlarm(
            "capacity-stable",
            name=f"{resource_name}-capacity-stable",
            namespace="AWS/AutoScaling",
            metric_name="GroupInServiceInstances",
            dimensions={"AutoScalingGroupName": asg_name},
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
            tags=tags,
            opts=child,
        )

        self.event_log = aws.cloudwatch.LogGroup(
            "event-log",
            name=f"/aws/events/{resource_name}",
            retention_in_days=7,
            tags=tags,
            opts=child,
        )
        aws.cloudwatch.LogResourcePolicy(
            "event-log-policy",
            policy_name=f"{resource_name}-asg-events",
            policy_document=self.event_log.arn.apply(_log_delivery_policy),
            opts=child,
        )

        scaling_events = aws.cloudwatch.EventRule(
            "scaling-events",
            name=f"{resource_name}-asg-events",
            description="every scaling event this group emits",
            event_pattern=_scaling_events_pattern(resource_name),
            tags=tags,
            opts=child,
        )
        aws.cloudwatch.EventTarget(
            "scaling-events-to-log",
            rule=scaling_events.name,
            target_id="log",
            arn=self.event_log.arn,
            opts=child,
        )

        alarm_events = aws.cloudwatch.EventRule(
            "alarm-events",
            name=f"{resource_name}-alarm-events",
            description="either failover alarm entering ALARM",
            event_pattern=pulumi.Output.all(no_capacity.name, capacity_stable.name).apply(
                lambda names: _alarm_events_pattern(list(names))
            ),
            tags=tags,
            opts=child,
        )
        aws.cloudwatch.EventTarget(
            "alarm-events-to-log",
            rule=alarm_events.name,
            target_id="log",
            arn=self.event_log.arn,
            opts=child,
        )

        role = aws.iam.Role(
            "failover-lambda",
            name=f"{resource_name}-failover-lambda",
            assume_role_policy=policies.assume_role("lambda.amazonaws.com"),
            tags=tags,
            opts=child,
        )
        aws.iam.RolePolicyAttachment(
            "failover-lambda-logs",
            role=role.name,
            policy_arn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
            opts=child,
        )
        aws.iam.RolePolicy(
            "failover-lambda-policy",
            role=role.name,
            policy=pulumi.Output.all(cluster.arn, self.alerts.arn).apply(
                lambda a: _lambda_policy(a[0], a[1], standby_name)
            ),
            opts=child,
        )

        self.function = aws.lambda_.Function(
            "failover-function",
            name=f"{resource_name}-failover",
            role=role.arn,
            runtime="python3.12",
            handler="handler.handler",
            code=pulumi.FileArchive(str(LAMBDA_SOURCE)),
            timeout=870,
            memory_size=256,
            reserved_concurrent_executions=1,
            environment={
                "variables": {
                    "CLUSTER": cluster.name,
                    "SERVICE": resource_name,
                    "STANDBY_NAME": standby_name,
                    "EIP_ALLOC": eip_id,
                    "DOMAIN": management_domain,
                    "HEALTH_URL": health_url,
                    "NO_CAPACITY_ALARM": f"{resource_name}-no-capacity",
                    "CAPACITY_STABLE_ALARM": f"{resource_name}-capacity-stable",
                    "TOPIC_ARN": self.alerts.arn,
                }
            },
            tags=tags,
            opts=child,
        )
        aws.cloudwatch.EventTarget(
            "alarm-events-to-lambda",
            rule=alarm_events.name,
            target_id="failover",
            arn=self.function.arn,
            opts=child,
        )
        aws.lambda_.Permission(
            "alarm-events-invoke",
            action="lambda:InvokeFunction",
            function=self.function.name,
            principal="events.amazonaws.com",
            source_arn=alarm_events.arn,
            opts=child,
        )

        task_events = aws.cloudwatch.EventRule(
            "task-events",
            name=f"{resource_name}-task-events",
            description="a task of this service reaching RUNNING",
            event_pattern=cluster.arn.apply(lambda arn: _task_events_pattern(arn, resource_name)),
            tags=tags,
            opts=child,
        )
        self.task_events_target = aws.cloudwatch.EventTarget(
            "task-events-to-lambda",
            rule=task_events.name,
            target_id="failover",
            arn=self.function.arn,
            opts=child,
        )
        self.task_events_invoke = aws.lambda_.Permission(
            "task-events-invoke",
            action="lambda:InvokeFunction",
            function=self.function.name,
            principal="events.amazonaws.com",
            source_arn=task_events.arn,
            opts=child,
        )

        self.topic_arn = self.alerts.arn
        self.function_name = self.function.name
        self.event_log_group = self.event_log.name
        self.register_outputs(
            {
                "topic_arn": self.topic_arn,
                "function_name": self.function_name,
                "event_log_group": self.event_log_group,
            }
        )
