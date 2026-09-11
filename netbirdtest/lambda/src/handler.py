import logging
import os
import ssl
import time
import urllib.error
import urllib.request

import boto3

ASG_NAME = os.environ["ASG_NAME"]
STANDBY_NAME = os.environ["STANDBY_NAME"]
DOMAIN = os.environ["DOMAIN"]
HEALTH_PATH = os.environ.get("HEALTH_PATH", "/oauth2")
NO_CAPACITY_ALARM = os.environ["NO_CAPACITY_ALARM"]
CAPACITY_STABLE_ALARM = os.environ["CAPACITY_STABLE_ALARM"]
TOPIC_ARN = os.environ["TOPIC_ARN"]

TAKEOVER = "/usr/local/bin/netbird-takeover"
STANDDOWN = "/usr/local/bin/netbird-standdown"

log = logging.getLogger()
log.setLevel(logging.INFO)

asg = boto3.client("autoscaling")
ec2 = boto3.client("ec2")
ssm = boto3.client("ssm")
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


def asg_group():
    groups = asg.describe_auto_scaling_groups(AutoScalingGroupNames=[ASG_NAME])
    return groups["AutoScalingGroups"][0]


def asg_instance_ids():
    return [i["InstanceId"] for i in asg_group()["Instances"]]


def asg_in_service():
    return [i["InstanceId"] for i in asg_group()["Instances"] if i["LifecycleState"] == "InService"]


def set_desired(count):
    asg.set_desired_capacity(
        AutoScalingGroupName=ASG_NAME, DesiredCapacity=count, HonorCooldown=False
    )


def wait_asg_empty(context, budget):
    deadline = time.time() + min(budget, remaining(context) - 60)
    while time.time() < deadline:
        left = asg_instance_ids()
        if not left:
            log.info("the group is empty")
            return
        log.info("waiting for %s to go away", left)
        time.sleep(10)
    raise Failed("the group still has instances after waiting")


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
    deadline = time.time() + min(budget, remaining(context) - 60)
    started = time.time()
    while time.time() < deadline:
        if healthy():
            log.info("%s answered after %ds", DOMAIN, time.time() - started)
            return True
        time.sleep(10)
    log.error("%s never answered", DOMAIN)
    return False


def run(instance_id, script, context, budget):
    sent = ssm.send_command(
        InstanceIds=[instance_id],
        DocumentName="AWS-RunShellScript",
        Parameters={"commands": [script]},
        TimeoutSeconds=600,
    )
    command_id = sent["Command"]["CommandId"]
    log.info("running %s on %s as %s", script, instance_id, command_id)
    deadline = time.time() + min(budget, remaining(context) - 45)
    while time.time() < deadline:
        time.sleep(5)
        try:
            result = ssm.get_command_invocation(CommandId=command_id, InstanceId=instance_id)
        except ssm.exceptions.InvocationDoesNotExist:
            continue
        if result["Status"] in ("Pending", "InProgress", "Delayed"):
            continue
        if result["Status"] == "Success":
            log.info("%s on %s finished", script, instance_id)
            return result["StandardOutputContent"]
        raise Failed(
            f"{script} on {instance_id} ended as {result['Status']}: "
            f"{result['StandardErrorContent'][:500]}"
        )
    raise Failed(f"{script} on {instance_id} did not finish in time")


def fail_over(context):
    instance_id, state = standby_instance()
    if instance_id is None:
        raise Failed(f"no instance tagged {STANDBY_NAME}")

    if state in ("pending", "running"):
        if asg_group()["DesiredCapacity"] != 1:
            set_desired(1)
        return f"standby {instance_id} is already {state}, nothing to do"

    log.info("fencing the group, standby %s is %s", instance_id, state)
    set_desired(0)
    wait_asg_empty(context, budget=240)

    log.info("starting standby %s", instance_id)
    ec2.start_instances(InstanceIds=[instance_id])
    ec2.get_waiter("instance_running").wait(
        InstanceIds=[instance_id], WaiterConfig={"Delay": 10, "MaxAttempts": 30}
    )

    served = wait_healthy(context, budget=420)
    log.info("unfencing the group")
    set_desired(1)
    if not served:
        raise Failed(f"standby {instance_id} started but {DOMAIN} is not serving")
    return f"switched to standby {instance_id}"


def fail_back(context):
    instance_id, state = standby_instance()
    if state != "running":
        return f"standby is {state}, nothing to do"

    primary = asg_in_service()
    if not primary:
        return "no instance in service, staying on the standby"
    primary_id = primary[0]
    log.info("handing %s back from standby %s", primary_id, instance_id)

    run(instance_id, STANDDOWN, context, budget=180)

    try:
        run(primary_id, TAKEOVER, context, budget=300)
        if not wait_healthy(context, budget=180):
            raise Failed(f"{primary_id} took over but {DOMAIN} is not serving")
    except Failed as e:
        log.error("takeover failed, rolling back: %s", e)
        roll_back(instance_id, primary_id, context)
        raise Failed(f"{e}. rolled back to the standby")

    log.info("stopping standby %s", instance_id)
    ec2.stop_instances(InstanceIds=[instance_id])
    return f"switched back to {primary_id}, standby {instance_id} stopping"


def roll_back(standby_id, primary_id, context):
    try:
        run(primary_id, STANDDOWN, context, budget=120)
    except Exception as e:
        notify(
            "netbird failback rollback needs attention",
            f"could not stop NetBird on {primary_id}: {e}\n"
            f"not restarting the standby, two writers would overwrite each other",
        )
        raise
    run(standby_id, TAKEOVER, context, budget=240)


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
