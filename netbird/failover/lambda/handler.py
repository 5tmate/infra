import json
import logging
import os
import ssl
import time
import urllib.request
from datetime import UTC, datetime

import boto3

CLUSTER = os.environ["CLUSTER"]
SERVICE = os.environ["SERVICE"]
ASG_NAME = os.environ["ASG_NAME"]
STANDBY_NAME = os.environ["STANDBY_NAME"]
EIP_ALLOC = os.environ["EIP_ALLOC"]
DOMAIN = os.environ["DOMAIN"]
HEALTH_URL = os.environ["HEALTH_URL"]
SKIP_HOLD_PATH = os.environ["SKIP_HOLD_PATH"]
POLL = 10
PRIMARY_READY_LIMIT = 420
HANDOFF_RESERVE = 90
MAX_HANDOFFS = 3
FAILED_STOPS = ("TaskFailedToStart", "EssentialContainerExited")

log = logging.getLogger()
log.setLevel(logging.INFO)

ecs = boto3.client("ecs")
ec2 = boto3.client("ec2")
ssm = boto3.client("ssm")
autoscaling = boto3.client("autoscaling")
functions = boto3.client("lambda")

once = set()


def ago(moment):
    return (datetime.now(UTC) - moment).total_seconds()


def healthy():
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=10, context=ssl.create_default_context()):
            return True
    except Exception:
        return False


def find_instances(filters):
    reservations = ec2.describe_instances(Filters=filters)["Reservations"]
    return {i["InstanceId"]: i for r in reservations for i in r["Instances"]}


def listed(operation, key, statuses, **params):
    arns = []
    for status in statuses:
        pages = ecs.get_paginator(operation).paginate(cluster=CLUSTER, **params, **status)
        arns += [arn for page in pages for arn in page[key]]
    arns = list(dict.fromkeys(arns))
    return [arns[i : i + 100] for i in range(0, len(arns), 100)]


def container_instances():
    statuses = [{"status": "ACTIVE"}, {"status": "DRAINING"}]
    batches = listed("list_container_instances", "containerInstanceArns", statuses)
    return {
        c["ec2InstanceId"]: c
        for batch in batches
        for c in ecs.describe_container_instances(cluster=CLUSTER, containerInstances=batch)[
            "containerInstances"
        ]
    }


def service_tasks():
    statuses = [{"desiredStatus": "RUNNING"}, {"desiredStatus": "STOPPED"}]
    batches = listed("list_tasks", "taskArns", statuses, serviceName=SERVICE)
    return [t for b in batches for t in ecs.describe_tasks(cluster=CLUSTER, tasks=b)["tasks"]]


def nowhere_left(zones):
    tried = []
    activities = autoscaling.describe_scaling_activities(AutoScalingGroupName=ASG_NAME)
    for activity in activities["Activities"]:
        if not activity["Description"].startswith("Launching"):
            continue
        if activity["StatusCode"] not in ("Failed", "Cancelled"):
            break
        tried.append(json.loads(activity.get("Details") or "{}").get("Availability Zone"))
    return bool(tried) and (None in tried or set(zones) <= set(tried))


class State:
    def __init__(self):
        asg = autoscaling.describe_auto_scaling_groups(AutoScalingGroupNames=[ASG_NAME])[
            "AutoScalingGroups"
        ][0]
        self.zones = asg["AvailabilityZones"]
        self.primaries = {i["InstanceId"]: i["LifecycleState"] for i in asg["Instances"]}
        self.hosts = container_instances()
        ids = sorted(set(self.primaries) | set(self.hosts))
        self.instances = find_instances([{"Name": "instance-id", "Values": ids}]) if ids else {}
        standby = find_instances(
            [
                {"Name": "tag:Name", "Values": [STANDBY_NAME]},
                {
                    "Name": "instance-state-name",
                    "Values": ["pending", "running", "stopping", "stopped"],
                },
            ]
        )
        self.instances.update(standby)
        newest = sorted(standby.values(), key=lambda i: i["LaunchTime"], reverse=True)
        self.standby = newest[0]["InstanceId"] if newest else None
        addresses = ec2.describe_addresses(AllocationIds=[EIP_ALLOC])["Addresses"]
        self.eip = addresses[0].get("InstanceId") if addresses else None
        by_arn = {c["containerInstanceArn"]: i for i, c in self.hosts.items()}
        self.tasks = service_tasks()
        for task in self.tasks:
            task["host"] = by_arn.get(task.get("containerInstanceArn"))
        self.running = [t for t in self.tasks if t["lastStatus"] == "RUNNING"]
        self.active = [t for t in self.tasks if t["lastStatus"] != "STOPPED"]
        only = self.running[0] if len(self.running) == 1 else None
        serving = only and only["desiredStatus"] == "RUNNING" and self.usable(only["host"])
        self.serving = only["host"] if serving else None
        self.ready = next(
            (i for i, v in self.primaries.items() if v == "InService" and self.usable(i)), None
        )
        self.coming = any(
            v == "InService" or v.startswith("Pending") for v in self.primaries.values()
        )
        self.health = None

    def state(self, instance_id):
        return self.instances.get(instance_id, {}).get("State", {}).get("Name", "gone")

    def age(self, instance_id):
        launched = self.instances.get(instance_id, {}).get("LaunchTime")
        return ago(launched) if launched else 0

    def usable(self, instance_id):
        host = self.hosts.get(instance_id, {})
        return (
            host.get("status") == "ACTIVE"
            and bool(host.get("agentConnected"))
            and self.state(instance_id) == "running"
        )

    def in_group(self, instance_id):
        tags = self.instances.get(instance_id, {}).get("Tags", [])
        return {"Key": "aws:autoscaling:groupName", "Value": ASG_NAME} in tags

    def task_on(self, instance_id):
        return next((t for t in self.active if t["host"] == instance_id), None)

    def answers(self):
        if self.health is None:
            self.health = healthy()
        return self.health


def set_state(host, status):
    log.info("setting %s to %s", host["ec2InstanceId"], status)
    ecs.update_container_instances_state(
        cluster=CLUSTER, containerInstances=[host["containerInstanceArn"]], status=status
    )


def mark_unhealthy(instance_id):
    autoscaling.set_instance_health(
        InstanceId=instance_id, HealthStatus="Unhealthy", ShouldRespectGracePeriod=False
    )


def agent_gone(instance_id):
    if healthy():
        return False
    time.sleep(POLL)
    host = container_instances().get(instance_id)
    return bool(host) and not host.get("agentConnected") and not healthy()


def hand_back(s, task):
    if task["taskArn"] not in once:
        once.add(task["taskArn"])
        try:
            command = ssm.send_command(
                InstanceIds=[s.standby],
                DocumentName="AWS-RunShellScript",
                Parameters={"commands": [f"touch {SKIP_HOLD_PATH}"]},
            )["Command"]["CommandId"]
            ssm.get_waiter("command_executed").wait(
                CommandId=command,
                InstanceId=s.standby,
                WaiterConfig={"Delay": 2, "MaxAttempts": 15},
            )
        except Exception:
            log.exception("the standby did not confirm the skip-hold marker")
    set_state(s.hosts[s.standby], "DRAINING")
    ecs.stop_task(cluster=CLUSTER, task=task["taskArn"], reason="failback to the primary")


def deregister(host):
    ecs.deregister_container_instance(
        cluster=CLUSTER, containerInstance=host["containerInstanceArn"], force=True
    )


def replace_primary(s):
    for instance_id, host in s.hosts.items():
        state = s.state(instance_id)
        if instance_id != s.standby and state in ("shutting-down", "terminated", "gone"):
            deregister(host)
            return f"removed {instance_id} from the cluster, its instance is {state}"
        if s.in_group(instance_id) and not host.get("agentConnected") and agent_gone(instance_id):
            if s.primaries.get(instance_id) == "InService":
                mark_unhealthy(instance_id)
            ec2.terminate_instances(InstanceIds=[instance_id])
            deregister(host)
            return f"{instance_id} lost its ECS agent and {DOMAIN} is silent, terminated it"
    for instance_id, lifecycle in s.primaries.items():
        host = s.hosts.get(instance_id)
        if lifecycle != "InService":
            continue
        if host and host["status"] == "DRAINING":
            mark_unhealthy(instance_id)
            return f"{instance_id} is draining, asked the group to replace it"
        stuck = not s.usable(instance_id) and not s.task_on(instance_id)
        if stuck and s.age(instance_id) > PRIMARY_READY_LIMIT:
            mark_unhealthy(instance_id)
            return (
                f"{instance_id} is not ready {s.age(instance_id):.0f}s after launch, replacing it"
            )
    return None


def continue_failback(s):
    standby = s.hosts.get(s.standby)
    if not standby or standby["status"] != "DRAINING" or s.state(s.standby) != "running":
        return None
    if not s.ready:
        set_state(standby, "ACTIVE")
        return "no primary can take the task, kept it on the standby"
    task = s.task_on(s.standby)
    if task:
        if task["desiredStatus"] != "RUNNING":
            return None
        hand_back(s, task)
        return f"stopping the task on the standby so it moves to {s.ready}"
    stops = [t["stoppingAt"] for t in s.tasks if t["host"] == s.standby and t.get("stoppingAt")]
    if not stops or s.serving == s.ready:
        return None
    failed = any(
        t["host"] == s.ready and t.get("stopCode") in FAILED_STOPS and t["createdAt"] >= max(stops)
        for t in s.tasks
    )
    if not failed:
        return None
    mark_unhealthy(s.ready)
    set_state(standby, "ACTIVE")
    return f"the task failed to start on {s.ready}, moved it back to the standby and replacing it"


def admit_standby(s):
    if s.coming or s.serving:
        return None
    if s.state(s.standby) != "stopped" or not nowhere_left(s.zones):
        return None
    host = s.hosts.get(s.standby)
    if host and host["status"] == "DRAINING":
        set_state(host, "ACTIVE")
    ec2.start_instances(InstanceIds=[s.standby])
    return f"the group has nowhere left to try, started standby {s.standby}"


def place_task(s):
    if s.active or "kick" in once or not any(s.usable(i) for i in s.hosts):
        return None
    once.add("kick")
    service = ecs.describe_services(cluster=CLUSTER, services=[SERVICE])["services"][0]
    if service["runningCount"] or service["pendingCount"]:
        return None
    ecs.update_service(cluster=CLUSTER, service=SERVICE, desiredCount=service["desiredCount"])
    return "the service had no task, asked ECS to place it now"


def route_eip(s):
    host = s.running[0]["host"] if len(s.running) == 1 else None
    if not host or s.state(host) != "running" or s.eip == host:
        return None
    ec2.associate_address(AllocationId=EIP_ALLOC, InstanceId=host, AllowReassociation=True)
    return f"moved the elastic ip to {host}"


def start_failback(s):
    if not s.ready or not s.serving or s.serving != s.standby:
        return None
    hand_back(s, s.task_on(s.standby))
    return f"{s.ready} is ready, moving the task back from standby {s.standby}"


def finish(s):
    if not s.serving or s.serving == s.standby or s.eip != s.serving:
        return None
    if s.state(s.standby) != "running" or s.standby not in s.hosts or not s.answers():
        return None
    ec2.stop_instances(InstanceIds=[s.standby])
    return f"the task runs on {s.serving} and {DOMAIN} answers, stopped standby {s.standby}"


def settled(s):
    if not s.serving or s.eip != s.serving or not s.answers():
        return False
    if s.serving == s.standby:
        return not s.coming
    return s.state(s.standby) not in ("pending", "running")


RULES = [
    replace_primary,
    continue_failback,
    admit_standby,
    place_task,
    route_eip,
    start_failback,
    finish,
]


def reconcile(context, hops):
    while True:
        s = State()
        for rule in RULES:
            done = rule(s)
            if done:
                log.info("%s: %s", rule.__name__, done)
                break
        else:
            if settled(s):
                return "settled"
        if context.get_remaining_time_in_millis() < HANDOFF_RESERVE * 1000:
            if hops >= MAX_HANDOFFS:
                return f"still not settled after {hops} hand-offs, waiting for the next event"
            functions.invoke(
                FunctionName=context.invoked_function_arn,
                InvocationType="Event",
                Payload=json.dumps({"handoff": hops + 1}),
            )
            return f"handed off to invocation {hops + 1}"
        time.sleep(POLL)


def handler(event, context):
    once.clear()
    hops = event.get("handoff", 0)
    log.info("woken by %s", event.get("detail-type") or f"hand-off {hops}")
    outcome = reconcile(context, hops)
    log.info("reconcile: %s", outcome)
    return {"outcome": outcome}
