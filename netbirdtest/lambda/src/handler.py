import logging
import os
import ssl
import time
import urllib.error
import urllib.request

import boto3

CLUSTER = os.environ["CLUSTER"]
SERVICE = os.environ["SERVICE"]
STANDBY_NAME = os.environ["STANDBY_NAME"]
EIP_ALLOC = os.environ["EIP_ALLOC"]
DOMAIN = os.environ["DOMAIN"]
HEALTH_PATH = os.environ.get("HEALTH_PATH", "/oauth2")
NO_CAPACITY_ALARM = os.environ["NO_CAPACITY_ALARM"]
CAPACITY_STABLE_ALARM = os.environ["CAPACITY_STABLE_ALARM"]
TOPIC_ARN = os.environ["TOPIC_ARN"]

log = logging.getLogger()
log.setLevel(logging.INFO)

ecs = boto3.client("ecs")
ec2 = boto3.client("ec2")
sns = boto3.client("sns")


class Failed(Exception):
    pass


def notify(subject, body):
    sns.publish(TopicArn=TOPIC_ARN, Subject=subject[:100], Message=body)


def remaining(context):
    return context.get_remaining_time_in_millis() / 1000.0


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


def claim_eip(instance_id):
    log.info("moving the elastic ip to %s", instance_id)
    ec2.associate_address(AllocationId=EIP_ALLOC, InstanceId=instance_id, AllowReassociation=True)


def healthy():
    url = f"https://{DOMAIN}{HEALTH_PATH}"
    try:
        with urllib.request.urlopen(url, timeout=10, context=ssl.create_default_context()) as r:
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


def fail_over(context):
    instance_id, state = standby_instance()
    if instance_id is None:
        raise Failed(f"no instance tagged {STANDBY_NAME}")
    if state in ("pending", "running"):
        return f"standby {instance_id} is already {state}, nothing to do"

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
        arn = (container_instances().get(instance_id) or {}).get("containerInstanceArn")
        if arn:
            break
        time.sleep(10)
    if not arn:
        raise Failed(f"standby {instance_id} never joined the cluster")

    wait_task_on(context, arn, budget=300)
    claim_eip(instance_id)
    if not wait_healthy(context, budget=180):
        raise Failed(f"the task runs on standby {instance_id} but {DOMAIN} is silent")
    return f"switched to standby {instance_id}"


def fail_back(context):
    instance_id, state = standby_instance()
    if state != "running":
        return f"standby is {state}, nothing to do"

    hosts = container_instances()
    standby_arn = (hosts.get(instance_id) or {}).get("containerInstanceArn")
    if standby_arn is None:
        return f"standby {instance_id} is not in the cluster, nothing to do"
    if running_task_host() != standby_arn:
        return "the task is not on the standby, nothing to do"

    primary = [c for i, c in hosts.items() if i != instance_id and c["status"] == "ACTIVE"]
    if not primary:
        return "no other container instance to hand back to, staying on the standby"
    primary_id = primary[0]["ec2InstanceId"]

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
        primary_arn = primary[0]["containerInstanceArn"]
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


def handler(event, context):
    detail = event.get("detail", {})
    name = detail.get("alarmName")
    state = detail.get("state", {}).get("value")
    log.info("alarm %s is %s", name, state)

    if state != "ALARM":
        return {"skipped": f"state is {state}"}

    if name == NO_CAPACITY_ALARM:
        action, run_it = "failover", fail_over
    elif name == CAPACITY_STABLE_ALARM:
        action, run_it = "failback", fail_back
    else:
        return {"skipped": f"unknown alarm {name}"}

    try:
        outcome = run_it(context)
    except Exception as e:
        log.exception("%s failed", action)
        notify(f"netbird {action} failed", f"{type(e).__name__}: {e}")
        raise

    log.info("%s: %s", action, outcome)
    if not outcome.endswith("nothing to do"):
        notify(f"netbird {action}", outcome)
    return {"action": action, "outcome": outcome}
