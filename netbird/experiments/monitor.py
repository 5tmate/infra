import argparse
import csv
import http.client
import os
import signal
import socket
import struct
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import boto3
from botocore.exceptions import BotoCoreError, ClientError

NAME = "5tmate-netbird"
REGION = "ap-northeast-1"
HEALTH_URL = "https://netbird.5tmate.threatreveal.org/oauth2/.well-known/openid-configuration"
STUN_HOST = "stun-netbird.5tmate.threatreveal.org"
AWS_FIELDS = ["stun", "eip", "asg", "standby", "task", "no_capacity", "activity"]
COLUMNS = ["time", "t_plus_s", "http", "http_code", "http_ms", *AWS_FIELDS, "aws_error"]
AWS_ERRORS = (BotoCoreError, ClientError)
_getaddrinfo = socket.getaddrinfo


def _ipv4_only(host, port, family=0, type=0, proto=0, flags=0):
    return _getaddrinfo(host, port, socket.AF_INET, type, proto, flags)


socket.getaddrinfo = _ipv4_only
START_KEYS = {"http", "eip", "asg", "task", "standby", "activity"}


def now():
    return datetime.now(timezone.utc).astimezone()


def stamp(t):
    return t.strftime("%H:%M:%S")


def offset(t, t0):
    if t0 is None:
        return ""
    s = int((t - t0).total_seconds())
    return f"T+{s // 60:02d}:{s % 60:02d}"


def probe_http():
    start = time.monotonic()
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=2.5) as r:
            code = r.status
    except urllib.error.HTTPError as e:
        code = e.code
    except (OSError, http.client.HTTPException):
        code = 0
    return code, int((time.monotonic() - start) * 1000)


def probe_stun():
    try:
        ip = socket.gethostbyname(STUN_HOST)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(2)
            s.sendto(struct.pack("!HHI", 1, 0, 0x2112A442) + os.urandom(12), (ip, 3478))
            s.recvfrom(2048)
    except OSError:
        return "no"
    return "ok"


class Aws:
    def __init__(self):
        session = boto3.Session(region_name=REGION)
        self.ec2 = session.client("ec2")
        self.asg = session.client("autoscaling")
        self.ecs = session.client("ecs")
        self.cw = session.client("cloudwatch")
        self.instance_of = {}

    def snapshot(self):
        snap = {"stun": probe_stun()}
        addr = self.ec2.describe_addresses(Filters=[{"Name": "tag:Name", "Values": [NAME]}])
        snap["eip"] = next((a.get("InstanceId", "-") for a in addr["Addresses"]), "none")

        groups = self.asg.describe_auto_scaling_groups(AutoScalingGroupNames=[NAME])
        members = groups["AutoScalingGroups"][0]["Instances"] if groups["AutoScalingGroups"] else []
        snap["asg"] = (
            " ".join(
                f"{m['InstanceId']}:{m['LifecycleState']}:{m['AvailabilityZone'][-2:]}"
                for m in members
            )
            or "-"
        )
        snap["primaries"] = {m["InstanceId"] for m in members if m["LifecycleState"] == "InService"}

        standby = self.ec2.describe_instances(
            Filters=[
                {"Name": "tag:Name", "Values": [f"{NAME}-standby"]},
                {
                    "Name": "instance-state-name",
                    "Values": ["pending", "running", "stopping", "stopped"],
                },
            ]
        )
        snap["standby"] = (
            " ".join(
                f"{i['InstanceId']}:{i['State']['Name']}"
                for r in standby["Reservations"]
                for i in r["Instances"]
            )
            or "-"
        )

        arns = self.ecs.list_tasks(cluster=NAME, serviceName=NAME, desiredStatus="RUNNING")[
            "taskArns"
        ]
        tasks = self.ecs.describe_tasks(cluster=NAME, tasks=arns)["tasks"] if arns else []
        snap["task"] = (
            " ".join(
                f"{t['lastStatus']}@{self._instance(t.get('containerInstanceArn'))}" for t in tasks
            )
            or "-"
        )

        alarms = self.cw.describe_alarms(AlarmNames=[f"{NAME}-no-capacity"])["MetricAlarms"]
        snap["no_capacity"] = alarms[0]["StateValue"] if alarms else "-"

        acts = self.asg.describe_scaling_activities(AutoScalingGroupName=NAME, MaxRecords=1)[
            "Activities"
        ]
        snap["activity"] = "-"
        if acts:
            a = acts[0]
            reason = f" ({a['StatusMessage'][:100]})" if a.get("StatusMessage") else ""
            snap["activity"] = f"{a['StatusCode']}: {a['Description'][:80]}{reason}"
        return snap

    def _instance(self, arn):
        if not arn:
            return "-"
        if arn not in self.instance_of:
            found = self.ecs.describe_container_instances(cluster=NAME, containerInstances=[arn])
            items = found["containerInstances"]
            self.instance_of[arn] = items[0]["ec2InstanceId"] if items else "?"
        return self.instance_of[arn]

    def details(self):
        lines = ["", "ASG activities (newest first):"]
        acts = self.asg.describe_scaling_activities(AutoScalingGroupName=NAME, MaxRecords=6)
        for a in acts["Activities"]:
            end = a.get("EndTime")
            took = f" took {int((end - a['StartTime']).total_seconds())}s" if end else ""
            when = stamp(a["StartTime"].astimezone())
            lines.append(f"  {when} {a['StatusCode']}{took}: {a['Description']}")
        arns = self.ecs.list_tasks(cluster=NAME, serviceName=NAME, desiredStatus="RUNNING")[
            "taskArns"
        ]
        for t in self.ecs.describe_tasks(cluster=NAME, tasks=arns)["tasks"] if arns else []:
            lines.append(f"Task on {self._instance(t.get('containerInstanceArn'))}:")
            for key in ["createdAt", "pullStartedAt", "pullStoppedAt", "startedAt"]:
                if key in t:
                    lines.append(f"  {key:14} {stamp(t[key].astimezone())}")
        return lines


class Monitor:
    def __init__(self, run_dir, events_file, writer, args):
        self.run_dir = run_dir
        self.events_file = events_file
        self.writer = writer
        self.args = args
        self.state = {"http_code": 0, "http_ms": 0, "aws_error": "", "primaries": set()}
        self.state.update(dict.fromkeys(AWS_FIELDS, "?"))
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.aws_ready = threading.Event()
        self.http_ready = threading.Event()
        self.t0 = None
        self.http_state = None
        self.http_since = None
        self.pending = None
        self.pending_since = None

    def http_loop(self):
        while not self.stop.is_set():
            code, ms = probe_http()
            with self.lock:
                self.state["http_code"], self.state["http_ms"] = code, ms
            self.http_ready.set()
            self.stop.wait(max(0.0, 1.0 - ms / 1000))

    def aws_loop(self):
        client = Aws()
        while not self.stop.is_set():
            try:
                snap = client.snapshot()
            except AWS_ERRORS as e:
                with self.lock:
                    self.state["aws_error"] = f"{type(e).__name__}: {str(e)[:120]}"
            else:
                with self.lock:
                    self.state.update(snap)
                    self.state["aws_error"] = ""
            self.aws_ready.set()
            self.stop.wait(5)

    def event(self, t, text):
        line = f"{stamp(t)} {offset(t, self.t0):9} {text}"
        print(line, flush=True)
        self.events_file.write(line + "\n")

    def run(self):
        threading.Thread(target=self.http_loop, daemon=True).start()
        threading.Thread(target=self.aws_loop, daemon=True).start()
        started = now()
        self.event(started, f"monitor started, logs in {self.run_dir}")
        self.aws_ready.wait(60)
        self.http_ready.wait(60)
        time.sleep(1)

        prev, down_since, healthy_since = None, None, None
        downs = []
        reason = "interrupted"
        while not self.stop.is_set():
            t = now()
            with self.lock:
                cur = dict(self.state)
            cur["http"] = self.debounce("UP" if cur["http_code"] == 200 else "DOWN", t)

            if prev is None:
                self.event(
                    t,
                    "baseline " + " | ".join(f"{k}={cur[k]}" for k in ["http", *AWS_FIELDS]),
                )
                if cur["aws_error"]:
                    self.event(t, f"cannot read AWS: {cur['aws_error']}")
            else:
                down_since = self.diff(t, prev, cur, down_since, downs)

            self.writer.writerow(
                [
                    t.isoformat(timespec="seconds"),
                    int((t - self.t0).total_seconds()) if self.t0 else "",
                ]
                + [cur[c] for c in COLUMNS[2:]]
            )

            recovered = (
                cur["http"] == "UP"
                and cur["eip"] in cur["primaries"]
                and not any(s in cur["standby"] for s in ("pending", "running", "stopping"))
            )
            healthy_since = (healthy_since or t) if recovered else None
            if (
                self.t0
                and healthy_since
                and (t - healthy_since).total_seconds() >= self.args.stable
            ):
                reason = f"primary healthy for {self.args.stable}s"
                self.stop.set()
            if (t - started).total_seconds() >= self.args.max_minutes * 60:
                reason = f"reached {self.args.max_minutes} minutes"
                self.stop.set()

            prev = cur
            self.stop.wait(max(0.0, 1.0 - (now() - t).total_seconds()))

        end = now()
        if down_since:
            downs.append((down_since, end))
        self.event(end, f"monitor stopped: {reason}")
        return started, end, reason, downs

    def debounce(self, raw, t):
        if self.http_state is None or raw == self.http_state:
            self.http_state = self.http_state or raw
            self.pending = None
        elif raw != self.pending:
            self.pending, self.pending_since = raw, t
        else:
            self.http_state, self.http_since, self.pending = (
                raw,
                self.pending_since,
                None,
            )
        return self.http_state

    def diff(self, t, prev, cur, down_since, downs):
        for key in ["http", *AWS_FIELDS, "aws_error"]:
            if cur[key] == prev[key] or prev[key] == "?" or (key == "aws_error" and not cur[key]):
                continue
            when = self.http_since if key == "http" else t
            if self.t0 is None and key in START_KEYS:
                self.t0 = when
                self.event(when, "test started (first change after baseline)")
            if key == "http" and cur["http"] == "DOWN":
                down_since = when
                self.event(when, f"http DOWN (code {cur['http_code']})")
            elif key == "http":
                took = int((when - down_since).total_seconds()) if down_since else 0
                if down_since:
                    downs.append((down_since, when))
                down_since = None
                self.event(when, f"http UP after {took}s down")
            else:
                self.event(t, f"{key}: {prev[key]} -> {cur[key]}")
        return down_since


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stable", type=int, default=180)
    parser.add_argument("--max-minutes", type=int, default=120)
    parser.add_argument("--out")
    args = parser.parse_args()

    run_dir = (
        Path(args.out)
        if args.out
        else Path(__file__).parent / "results" / now().strftime("%Y%m%d-%H%M%S")
    )
    run_dir.mkdir(parents=True, exist_ok=True)
    with (
        (run_dir / "events.log").open("w", buffering=1) as events_file,
        (run_dir / "probe.csv").open("w", newline="", buffering=1) as csv_file,
    ):
        writer = csv.writer(csv_file)
        writer.writerow(COLUMNS)
        monitor = Monitor(run_dir, events_file, writer, args)
        signal.signal(signal.SIGINT, lambda *_: monitor.stop.set())
        signal.signal(signal.SIGTERM, lambda *_: monitor.stop.set())
        started, end, reason, downs = monitor.run()

    total = sum(int((b - a).total_seconds()) for a, b in downs)
    summary = [
        f"run            {started:%Y-%m-%d %H:%M:%S} to {end:%H:%M:%S}",
        f"test started   {stamp(monitor.t0) if monitor.t0 else 'no change seen'}",
        f"stopped        {reason}",
        f"outages        {len(downs)}, total {total}s",
    ]
    summary += [f"  {stamp(a)} to {stamp(b)}  {int((b - a).total_seconds())}s" for a, b in downs]
    head = len(summary)
    try:
        summary += Aws().details()
    except AWS_ERRORS as e:
        summary.append(f"could not read AWS details: {e}")
    summary += ["", "Events:", *(run_dir / "events.log").read_text().splitlines()]
    (run_dir / "summary.txt").write_text("\n".join(summary) + "\n")
    print("\n".join(summary[:head]))
    print(f"summary written to {run_dir / 'summary.txt'}")


if __name__ == "__main__":
    main()
