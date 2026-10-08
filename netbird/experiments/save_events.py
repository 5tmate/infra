import re
import socket
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import boto3

NAME = "5tmate-netbird"
REGION = "ap-northeast-1"
_getaddrinfo = socket.getaddrinfo


def _ipv4_only(host, port, family=0, type=0, proto=0, flags=0):
    return _getaddrinfo(host, port, socket.AF_INET, type, proto, flags)


socket.getaddrinfo = _ipv4_only


def local(t):
    return t.astimezone().strftime("%H:%M:%S")


def window(run):
    rows = (run / "probe.csv").read_text().splitlines()[1:]
    start = datetime.fromisoformat(rows[0].split(",")[0]) - timedelta(minutes=1)
    end = datetime.fromisoformat(rows[-1].split(",")[0]) + timedelta(minutes=1)
    return start, end


def ecs_service_events(ecs, start, end):
    events = ecs.describe_services(cluster=NAME, services=[NAME])["services"][0]["events"]
    hits = sorted(
        (e for e in events if start <= e["createdAt"] <= end),
        key=lambda e: e["createdAt"],
    )
    return [f"{local(e['createdAt'])} {e['message']}" for e in hits]


def asg_activities(asg, start, end):
    lines = []
    for page in asg.get_paginator("describe_scaling_activities").paginate(
        AutoScalingGroupName=NAME
    ):
        for a in page["Activities"]:
            if start <= a["StartTime"] <= end:
                took = (
                    f" took {int((a['EndTime'] - a['StartTime']).total_seconds())}s"
                    if a.get("EndTime")
                    else ""
                )
                text = f"{local(a['StartTime'])} {a['StatusCode']}{took}: {a['Description']}"
                lines.append((a["StartTime"], f"{text}\n    {a['Cause']}"))
    return [text for _, text in sorted(lines)]


def ecs_tasks(ecs, start, end):
    arns = []
    for status in ("RUNNING", "STOPPED"):
        arns += ecs.list_tasks(cluster=NAME, desiredStatus=status)["taskArns"]
    lines = []
    for i in range(0, len(arns), 100):
        for t in ecs.describe_tasks(cluster=NAME, tasks=arns[i : i + 100])["tasks"]:
            stamps = [t.get(k) for k in ("createdAt", "stoppedAt")]
            if not any(s and start <= s <= end for s in stamps):
                continue
            parts = [f"task {t['taskArn'].rsplit('/', 1)[-1][:8]} {t['lastStatus']}"]
            for key in (
                "createdAt",
                "pullStartedAt",
                "pullStoppedAt",
                "startedAt",
                "stoppingAt",
                "stoppedAt",
            ):
                if key in t:
                    parts.append(f"  {key:14} {local(t[key])}")
            if t.get("stoppedReason"):
                parts.append(f"  stoppedReason  {t['stoppedReason']}")
            lines.append((t["createdAt"], "\n".join(parts)))
    return [text for _, text in sorted(lines)]


def transition_time(instance):
    m = re.search(
        r"\((\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) GMT\)",
        instance.get("StateTransitionReason", ""),
    )
    return datetime.strptime(m[1], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc) if m else None


def ec2_instances(ec2, start, end):
    found = ec2.describe_instances(
        Filters=[{"Name": "tag:Name", "Values": [NAME, f"{NAME}-standby"]}]
    )
    lines = []
    for r in found["Reservations"]:
        for i in r["Instances"]:
            changed = transition_time(i)
            if not (start <= i["LaunchTime"] <= end or (changed and start <= changed <= end)):
                continue
            text = (
                f"{i['InstanceId']} {i['InstanceType']} {i.get('InstanceLifecycle', 'on-demand')} "
                f"launched {local(i['LaunchTime'])}, now {i['State']['Name']}"
            )
            if changed:
                text += f" since {local(changed)}"
            if reason := i.get("StateReason", {}).get("Message"):
                text += f"\n    {reason}"
            lines.append((i["LaunchTime"], text))
    return [text for _, text in sorted(lines)]


def main():
    session = boto3.Session(region_name=REGION)
    ecs, asg = session.client("ecs"), session.client("autoscaling")
    ec2 = session.client("ec2")
    for arg in sys.argv[1:]:
        run = Path(arg)
        start, end = window(run)
        outputs = {
            "ecs-service-events.log": ecs_service_events(ecs, start, end),
            "asg-activities.log": asg_activities(asg, start, end),
            "ecs-tasks.log": ecs_tasks(ecs, start, end),
            "ec2-instances.log": ec2_instances(ec2, start, end),
        }
        for name, lines in outputs.items():
            (run / name).write_text("\n".join(lines) + "\n")
        print(f"{run.name}: " + ", ".join(f"{n} {len(v)}" for n, v in outputs.items()))


if __name__ == "__main__":
    main()
