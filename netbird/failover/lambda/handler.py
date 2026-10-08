import logging
import os
import ssl
import time
import urllib.error
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
NO_CAPACITY_ALARM = os.environ["NO_CAPACITY_ALARM"]
TOPIC_ARN = os.environ["TOPIC_ARN"]
FAILOVER_EVENT_MAX_AGE = 300
PRIMARY_JOIN_BUDGET = 120
FAILBACK_SOAK = 180
DISCONNECT_RECHECK = 10

log = logging.getLogger()
log.setLevel(logging.INFO)

ecs = boto3.client("ecs")
ec2 = boto3.client("ec2")
sns = boto3.client("sns")
autoscaling = boto3.client("autoscaling")


class Failed(Exception):
    pass


def notify(subject, body):
    sns.publish(TopicArn=TOPIC_ARN, Subject=subject[:100], Message=body)


def remaining(context):
    return context.get_remaining_time_in_millis() / 1000.0


def event_age(event):
    sent = datetime.fromisoformat(event["time"].replace("Z", "+00:00"))
    return (datetime.now(UTC) - sent).total_seconds()


def group():
    return autoscaling.describe_auto_scaling_groups(AutoScalingGroupNames=[ASG_NAME])[
        "AutoScalingGroups"
    ][0]


def in_service(asg):
    return [i["InstanceId"] for i in asg["Instances"] if i["LifecycleState"] == "InService"]


def standby_instance():
    pages = ec2.get_paginator("describe_instances").paginate(
        Filters=[
            {"Name": "tag:Name", "Values": [STANDBY_NAME]},
            {
                "Name": "instance-state-name",
                "Values": ["pending", "running", "stopping", "stopped"],
            },
        ]
    )
    for page in pages:
        for reservation in page["Reservations"]:
            for instance in reservation["Instances"]:
                return instance["InstanceId"], instance["State"]["Name"]
    return None, None


def container_instances():
    arns = ecs.list_container_instances(cluster=CLUSTER, status="ACTIVE")["containerInstanceArns"]
    arns += ecs.list_container_instances(cluster=CLUSTER, status="DRAINING")[
        "containerInstanceArns"
    ]
    if not arns:
        return {}
    described = ecs.describe_container_instances(cluster=CLUSTER, containerInstances=arns)[
        "containerInstances"
    ]
    return {c["ec2InstanceId"]: c for c in described}


def usable(container_instance):
    return container_instance["status"] == "ACTIVE" and container_instance.get("agentConnected")


def running_task_host():
    arns = ecs.list_tasks(cluster=CLUSTER, serviceName=SERVICE, desiredStatus="RUNNING")["taskArns"]
    if not arns:
        return None
    tasks = ecs.describe_tasks(cluster=CLUSTER, tasks=arns)["tasks"]
    for task in tasks:
        if task["lastStatus"] == "RUNNING":
            return task.get("containerInstanceArn")
    return None


def wait_task_on(context, wanted, budget, equal=True):
    deadline = time.time() + min(budget, remaining(context) - 60)
    while time.time() < deadline:
        host = running_task_host()
        if host and ((host == wanted) if equal else (host != wanted)):
            log.info("the task is running on %s", host)
            return host
        log.info("the task is on %s, waiting", host)
        time.sleep(10)
    raise Failed("the service never placed a running task where it was needed")


def eip_holder():
    addresses = ec2.describe_addresses(AllocationIds=[EIP_ALLOC])["Addresses"]
    return addresses[0].get("InstanceId") if addresses else None


def claim_eip(instance_id):
    log.info("moving the elastic ip to %s", instance_id)
    ec2.associate_address(AllocationId=EIP_ALLOC, InstanceId=instance_id, AllowReassociation=True)


def healthy():
    try:
        with urllib.request.urlopen(
            HEALTH_URL, timeout=10, context=ssl.create_default_context()
        ) as r:
            return 200 <= r.status < 300
    except urllib.error.HTTPError as e:
        return 200 <= e.code < 300
    except Exception:
        return False


def wait_healthy(context, budget):
    deadline = time.time() + min(budget, remaining(context) - 45)
    started = time.time()
    while time.time() < deadline:
        if healthy():
            log.info("%s answered after %ds", DOMAIN, time.time() - started)
            return True
        time.sleep(10)
    log.error("%s never answered", DOMAIN)
    return False


def set_state(container_instance_arn, state):
    log.info("setting %s to %s", container_instance_arn, state)
    ecs.update_container_instances_state(
        cluster=CLUSTER, containerInstances=[container_instance_arn], status=state
    )


def place_eip(event):
    detail = event.get("detail", {})
    arn = detail.get("containerInstanceArn")
    if not arn:
        return "the event carries no container instance, nothing to do"
    if detail.get("desiredStatus") != "RUNNING":
        return f"the task is meant to be {detail.get('desiredStatus')}, nothing to do"
    if running_task_host() != arn:
        return f"the service's running task is not on {arn}, nothing to do"

    hosts = ecs.describe_container_instances(cluster=CLUSTER, containerInstances=[arn])[
        "containerInstances"
    ]
    if not hosts:
        return f"{arn} is no longer in the cluster, nothing to do"

    instance_id = hosts[0]["ec2InstanceId"]
    reservations = ec2.describe_instances(InstanceIds=[instance_id])["Reservations"]
    state = reservations[0]["Instances"][0]["State"]["Name"] if reservations else "gone"
    if state != "running":
        return f"{instance_id} is {state}, nothing to do"
    if eip_holder() == instance_id:
        return f"the elastic ip is already on {instance_id}, nothing to do"

    claim_eip(instance_id)
    return f"moved the elastic ip to {instance_id}"


def kick_service():
    service = ecs.describe_services(cluster=CLUSTER, services=[SERVICE])["services"][0]
    if service["runningCount"] or service["pendingCount"]:
        return "the service already has a task, nothing to do"
    ecs.update_service(cluster=CLUSTER, service=SERVICE, desiredCount=service["desiredCount"])
    return "the service had no task, asked ECS to place it now"


def fail_over(context, check_health=True):
    if check_health and healthy():
        return f"{DOMAIN} still answers, nothing to do"
    instance_id, state = standby_instance()
    if instance_id is None:
        raise Failed(f"no instance tagged {STANDBY_NAME}")

    if state not in ("pending", "running"):
        existing = container_instances().get(instance_id)
        if existing and existing["status"] == "DRAINING":
            set_state(existing["containerInstanceArn"], "ACTIVE")
        log.info("starting standby %s", instance_id)
        ec2.start_instances(InstanceIds=[instance_id])

    ec2.get_waiter("instance_running").wait(
        InstanceIds=[instance_id], WaiterConfig={"Delay": 10, "MaxAttempts": 30}
    )

    deadline = time.time() + min(300, remaining(context) - 240)
    arn = None
    while time.time() < deadline:
        found = container_instances().get(instance_id)
        if found and usable(found):
            arn = found["containerInstanceArn"]
            break
        log.info("standby %s has not reconnected to the cluster yet", instance_id)
        time.sleep(10)
    if not arn:
        raise Failed(f"standby {instance_id} never joined the cluster")

    if running_task_host() != arn:
        log.info("deploy: %s", kick_service())
        wait_task_on(context, arn, budget=300)

    if eip_holder() != instance_id:
        claim_eip(instance_id)

    if not wait_healthy(context, budget=180):
        raise Failed(f"the task runs on standby {instance_id} but {DOMAIN} is silent")
    return f"switched to standby {instance_id}"


def launch_failed(event, context):
    age = event_age(event)
    if age > FAILOVER_EVENT_MAX_AGE:
        return f"the failed launch is {age:.0f}s old, nothing to do"
    asg = group()
    if asg["DesiredCapacity"] != 1:
        return f"desired capacity is {asg['DesiredCapacity']}, nothing to do"
    if in_service(asg):
        return f"{in_service(asg)[0]} is in service, nothing to do"
    if task_on_standby():
        return "the task already runs on the standby, nothing to do"
    return fail_over(context, check_health=False)


def agent_gone(instance_id):
    if healthy():
        return False
    time.sleep(DISCONNECT_RECHECK)
    host = container_instances().get(instance_id)
    return bool(host) and not host.get("agentConnected") and not healthy()


def mark_unhealthy(instance_id):
    autoscaling.set_instance_health(
        InstanceId=instance_id, HealthStatus="Unhealthy", ShouldRespectGracePeriod=False
    )


def replace_primary(event):
    instance_id = event.get("detail", {}).get("ec2InstanceId")
    if not instance_id:
        return "the event names no instance, nothing to do"
    if instance_id not in in_service(group()):
        return f"{instance_id} is not in service in the group, nothing to do"
    host = container_instances().get(instance_id)
    if not host:
        return f"{instance_id} is not in the cluster, nothing to do"
    if host["status"] == "DRAINING":
        mark_unhealthy(instance_id)
        return f"{instance_id} is draining while in service, asked the group to replace it"
    if host.get("agentConnected") or not agent_gone(instance_id):
        return f"{instance_id} is neither draining nor down, nothing to do"
    mark_unhealthy(instance_id)
    ecs.deregister_container_instance(
        cluster=CLUSTER, containerInstance=host["containerInstanceArn"], force=True
    )
    return (
        f"{instance_id} lost its ECS agent and {DOMAIN} is silent, "
        "asked the group to replace it and deregistered it so its task stops"
    )


def primary_ready(instance_id):
    if instance_id not in in_service(group()):
        return False
    host = container_instances().get(instance_id)
    return bool(host and usable(host))


def task_on_standby():
    instance_id, state = standby_instance()
    if state != "running":
        return False
    standby = container_instances().get(instance_id)
    return bool(standby) and running_task_host() == standby["containerInstanceArn"]


def launch_succeeded(event, context):
    primary_id = event.get("detail", {}).get("EC2InstanceId")
    if not primary_id:
        return "the launch names no instance, nothing to do"
    if not task_on_standby():
        return "the task is not on the standby, nothing to do"

    deadline = time.time() + PRIMARY_JOIN_BUDGET
    while not primary_ready(primary_id):
        if time.time() > deadline:
            return f"{primary_id} never joined the cluster, staying on the standby"
        log.info("waiting for %s to join the cluster", primary_id)
        time.sleep(20)

    ready_since = time.time()
    while time.time() - ready_since < FAILBACK_SOAK:
        time.sleep(20)
        if not primary_ready(primary_id):
            return f"{primary_id} stopped being ready, staying on the standby"
    log.info("%s stayed ready for %ds", primary_id, FAILBACK_SOAK)
    return fail_back(context, primary_id)


def fail_back(context, primary_id):
    instance_id, state = standby_instance()
    if state != "running":
        return f"standby is {state}, nothing to do"

    hosts = container_instances()
    standby_arn = (hosts.get(instance_id) or {}).get("containerInstanceArn")
    if standby_arn is None:
        return f"standby {instance_id} is not in the cluster, nothing to do"
    if running_task_host() != standby_arn:
        return "the task is not on the standby, nothing to do"

    primary = hosts.get(primary_id)
    if not primary or not usable(primary):
        return f"{primary_id} is not ready, staying on the standby"
    primary_arn = primary["containerInstanceArn"]

    log.info("draining %s so the task moves back to %s", instance_id, primary_id)
    set_state(standby_arn, "DRAINING")

    try:
        wait_task_on(context, standby_arn, budget=300, equal=False)
        claim_eip(primary_id)
        if not wait_healthy(context, budget=180):
            raise Failed(f"the task moved to {primary_id} but {DOMAIN} is silent")
    except Failed as e:
        log.error("handing back failed, pushing the task onto the standby again: %s", e)
        set_state(standby_arn, "ACTIVE")
        set_state(primary_arn, "DRAINING")
        try:
            wait_task_on(context, standby_arn, budget=240)
            claim_eip(instance_id)
        finally:
            set_state(primary_arn, "ACTIVE")
        raise Failed(f"{e}. rolled the task back onto the standby")

    log.info("stopping standby %s", instance_id)
    ec2.stop_instances(InstanceIds=[instance_id])
    return f"handed back to {primary_id}, standby {instance_id} stopping"


def run(action, step):
    try:
        outcome = step()
    except Exception as e:
        log.exception("%s failed", action)
        notify(f"netbird {action} failed", f"{type(e).__name__}: {e}")
        raise

    log.info("%s: %s", action, outcome)
    if not outcome.endswith("nothing to do"):
        notify(f"netbird {action}", outcome)
    return {"action": action, "outcome": outcome}


def handler(event, context):
    kind = event.get("detail-type")
    if kind == "ECS Task State Change":
        log.info("a task reached %s", event.get("detail", {}).get("lastStatus"))
        outcome = place_eip(event)
        log.info("eip: %s", outcome)
        return {"action": "eip", "outcome": outcome}

    if kind == "ECS Container Instance State Change":
        detail = event.get("detail", {})
        log.info(
            "%s is %s, agent connected: %s",
            detail.get("ec2InstanceId"),
            detail.get("status"),
            detail.get("agentConnected"),
        )
        return run("replace", lambda: replace_primary(event))

    if kind == "EC2 Instance Launch Unsuccessful":
        log.info("the group could not launch an instance")
        return run("failover", lambda: launch_failed(event, context))

    if kind == "EC2 Instance Launch Successful":
        log.info("the group launched %s", event.get("detail", {}).get("EC2InstanceId"))
        return run("failback", lambda: launch_succeeded(event, context))

    if kind != "CloudWatch Alarm State Change":
        return {"skipped": f"{kind} needs no action"}

    detail = event.get("detail", {})
    name = detail.get("alarmName")
    state = detail.get("state", {}).get("value")
    log.info("alarm %s is %s", name, state)
    if state != "ALARM":
        return {"skipped": f"state is {state}"}
    if name != NO_CAPACITY_ALARM:
        return {"skipped": f"unknown alarm {name}"}
    return run("failover", lambda: fail_over(context))
