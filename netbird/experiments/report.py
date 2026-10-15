import json
import re
import socket
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import boto3

NAME = "5tmate-netbird"
REGION = "ap-northeast-1"
TW = datetime.now().astimezone().tzinfo
NOISE = ("Describe", "List", "Get", "Lookup")
SKIP = {
    "Decrypt",
    "GenerateDataKey",
    "CreateGrant",
    "CreateLogStream",
    "AssumeRole",
    "CreateOAuth2Token",
    "FilterLogEvents",
    "StartQuery",
}
_getaddrinfo = socket.getaddrinfo


def _ipv4(host, port, family=0, type=0, proto=0, flags=0):
    return _getaddrinfo(host, port, socket.AF_INET, type, proto, flags)


socket.getaddrinfo = _ipv4


def hms(t):
    return t.astimezone(TW).strftime("%H:%M:%S")


def read_probe(run):
    date = re.search(r"(\d{8})", run.name)
    day = datetime.strptime(date.group(1), "%Y%m%d").date() if date else datetime.now().date()
    rows = []
    for line in (run / "probe.txt").read_text().splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        t = datetime.strptime(parts[0], "%H:%M:%S").time()
        rows.append((datetime.combine(day, t, TW), parts[1]))
    return rows


def outages(rows):
    downs, singles = [], []
    state, prev, run_start, down_since = True, None, None, None
    for dt, code in rows:
        ok = code == "200"
        if ok != prev:
            run_start, run = dt, 1
        else:
            run += 1
        prev = ok
        if not ok and state:
            singles.append((dt, code))
        if run >= 2 and ok != state:
            state = ok
            if not ok:
                down_since = run_start
            else:
                downs.append((down_since, run_start))
                down_since = None
    if down_since:
        downs.append((down_since, rows[-1][0]))
    spans = {(a, b) for a, b in downs}
    singles = [(dt, code) for dt, code in singles if not any(a <= dt <= b for a, b in spans)]
    codes = {}
    for a, b in downs:
        codes[(a, b)] = sorted({c for dt, c in rows if a <= dt <= b and c != "200"})
    return downs, singles, codes


def paginate(client, op, key, **kw):
    out, token = [], None
    while True:
        if token:
            kw["NextToken"] = token
        resp = getattr(client, op)(**kw)
        out += resp[key]
        token = resp.get("NextToken")
        if not token:
            return out


def lookup(ct, start, end, key, value):
    return paginate(
        ct,
        "lookup_events",
        "Events",
        StartTime=start,
        EndTime=end,
        MaxResults=50,
        LookupAttributes=[{"AttributeKey": key, "AttributeValue": value}],
    )


def az_of(activity):
    return (json.loads(activity.get("Details") or "{}").get("Availability Zone") or "?")[-2:]


def asg_section(asg, start, end):
    acts = [
        a
        for a in paginate(
            asg, "describe_scaling_activities", "Activities", AutoScalingGroupName=NAME
        )
        if start <= a["StartTime"] <= end
    ]
    acts.sort(key=lambda a: a["StartTime"])
    rows, events, n, last = [], [], 0, None
    for a in acts:
        launching = a["Description"].startswith("Launching")
        az = az_of(a) if launching else "-"
        label, gap = "", ""
        if launching:
            n += 1
            label = f"第{n}次"
            gap = f"{round((a['StartTime'] - last).total_seconds())}秒" if last else ""
            last = a["StartTime"]
        msg = (a.get("StatusMessage") or a["Description"])[:70]
        rows.append((hms(a["StartTime"]), label, a["StatusCode"], az, gap, msg))
        if launching and a["StatusCode"] in ("Failed", "Cancelled"):
            events.append((a["StartTime"], f"ASG {label} {az} 失敗"))
        else:
            inst = re.search(r"i-[0-9a-f]+", a["Description"])
            verb = "開出" if launching else "終止"
            events.append((a["StartTime"], f"ASG {verb} {inst.group() if inst else ''}".strip()))
    return rows, events


def host_map(ecs, arns, registrations):
    m = {}
    arns = [a for a in arns if a]
    for i in range(0, len(arns), 100):
        d = ecs.describe_container_instances(cluster=NAME, containerInstances=arns[i : i + 100])
        for c in d["containerInstances"]:
            m[c["containerInstanceArn"]] = c["ec2InstanceId"]
    for e in registrations:
        ci = (e.get("responseElements") or {}).get("containerInstance") or {}
        if ci.get("containerInstanceArn") and ci.get("ec2InstanceId"):
            m.setdefault(ci["containerInstanceArn"], ci["ec2InstanceId"])
    return m


def task_section(ecs, start, end, registrations):
    arns = []
    for status in ("RUNNING", "STOPPED"):
        arns += paginate(
            ecs, "list_tasks", "taskArns", cluster=NAME, serviceName=NAME, desiredStatus=status
        )
    tasks = []
    for i in range(0, len(arns), 100):
        tasks += ecs.describe_tasks(cluster=NAME, tasks=arns[i : i + 100])["tasks"]
    tasks = [t for t in tasks if start <= t["createdAt"] <= end]
    tasks.sort(key=lambda t: t["createdAt"])
    hosts = host_map(ecs, [t.get("containerInstanceArn") for t in tasks], registrations)
    rows, events = [], []
    for t in tasks:
        host = hosts.get(t.get("containerInstanceArn"), (t.get("containerInstanceArn") or "?")[-8:])
        short = t["taskArn"][-8:]

        def at(k):
            return hms(t[k]) if t.get(k) else ""

        rows.append(
            (
                short,
                host,
                at("createdAt"),
                at("startedAt"),
                at("stoppingAt"),
                at("stoppedAt"),
                t.get("stopCode", ""),
                (t.get("stoppedReason") or "")[:45],
            )
        )
        events.append((t["createdAt"], f"task {short} 建立在 {host}"))
        if t.get("startedAt"):
            events.append((t["startedAt"], f"task {short} 跑起來"))
        if t.get("stoppedAt"):
            events.append((t["stoppedAt"], f"task {short} 停止"))
    return rows, events


def summarize(name, rp):
    def ids(key):
        v = rp.get(key, {})
        items = v.get("items", v) if isinstance(v, dict) else v
        items = items if isinstance(items, list) else [items]
        return " ".join(x.get("instanceId", "") for x in items if isinstance(x, dict))

    if name == "AssociateAddress":
        return f"EIP 到 {rp.get('instanceId')}"
    if name in ("StartInstances", "StopInstances"):
        return f"{name[:-9].lower()} {ids('instancesSet')}".strip()
    if name == "TerminateInstances":
        return f"終止 {ids('instancesSet')}"
    if name == "TerminateInstanceInAutoScalingGroup":
        return f"終止 {rp.get('instanceId')}"
    if name == "SetInstanceHealth":
        return f"{rp.get('instanceId')} {rp.get('healthStatus')}"
    if name == "UpdateContainerInstancesState":
        ci = (rp.get("containerInstances") or [""])[0][-8:]
        return f"{rp.get('status')} {ci}"
    if name == "StopTask":
        return f"停 task {str(rp.get('task'))[-8:]} ({rp.get('reason')})"
    if name == "UpdateService":
        return "叫 ECS 排 task"
    if name == "SendCommand":
        return f"SSM {rp.get('documentName')}"
    if name in ("StartExperiment", "CreateExperimentTemplate", "DeleteExperimentTemplate"):
        return f"FIS {name}"
    return json.dumps(rp)[:60]


def actor_label(ev, me):
    u = ev.get("Username", "")
    if u == f"{NAME}-failover":
        return "Lambda"
    if u == me:
        return "我"
    return u or "ASG"


def cloudtrail_section(ct, start, end, me):
    seen, rows, events = set(), [], []
    users = [f"{NAME}-failover", me]
    allev = []
    for u in users:
        allev += lookup(ct, start, end, "Username", u)
    for ev in allev:
        name = ev["EventName"]
        if name.startswith(NOISE) or name in SKIP:
            continue
        detail = json.loads(ev["CloudTrailEvent"])
        rp = detail.get("requestParameters") or {}
        txt = summarize(name, rp)
        who = actor_label(ev, me)
        key = (ev["EventTime"], name, who)
        if key in seen:
            continue
        seen.add(key)
        rows.append((hms(ev["EventTime"]), who, name, txt))
        events.append((ev["EventTime"], f"{who} {txt}"))
    rows.sort()
    return rows, events


def table(header, rows):
    out = ["| " + " | ".join(header) + " |", "| " + " | ".join("---" for _ in header) + " |"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def main():
    run = Path(sys.argv[1])
    rows = read_probe(run)
    start = (rows[0][0] - timedelta(minutes=2)).astimezone(timezone.utc)
    end = (rows[-1][0] + timedelta(minutes=2)).astimezone(timezone.utc)
    s = boto3.Session(region_name=REGION)
    ct = s.client("cloudtrail")
    asg = s.client("autoscaling")
    ecs = s.client("ecs")
    me = s.client("sts").get_caller_identity()["Arn"].split("/")[-1]
    registrations = lookup(
        ct, start - timedelta(minutes=20), end, "EventName", "RegisterContainerInstance"
    )

    downs, singles, codes = outages(rows)
    asg_rows, asg_ev = asg_section(asg, start, end)
    task_rows, task_ev = task_section(ecs, start, end, registrations)
    ct_rows, ct_ev = cloudtrail_section(ct, start, end, me)

    out = [
        f"# {run.name} 自動產生的資料",
        "",
        "目的、預期和結論寫在當天的 `<date>.md`。",
        "這份由 `report.py` 產生，時間都是台灣時間。",
        "",
    ]

    out += ["## 操作", ""]
    actions = run / "actions.log"
    out += [
        table(["時間", "內容"], [(ln[:8], ln[9:]) for ln in actions.read_text().splitlines()])
        if actions.exists()
        else "沒有 actions.log。",
        "",
    ]

    out += ["## 斷線", ""]
    drows = [
        (hms(a), hms(b), f"{int((b - a).total_seconds())}秒", " ".join(codes[(a, b)]))
        for a, b in downs
    ]
    out += [table(["開始", "恢復", "斷線", "回應碼"], drows) if drows else "沒有量到斷線。"]
    if singles:
        joined = "、".join(f"{hms(t)} {c}" for t, c in singles)
        out += ["", "單次逾時（前後都正常，不算斷線）：" + joined]
    out += [""]

    timeline = sorted(
        asg_ev + task_ev + ct_ev + [(a, "斷線") for a, _ in downs] + [(b, "恢復") for _, b in downs]
    )
    out += ["## 時間線", "", table(["時間", "事件"], [(hms(t), txt) for t, txt in timeline]), ""]

    out += [
        "## ASG 活動",
        "",
        table(["開始", "第幾次", "結果", "AZ", "間隔", "內容"], asg_rows),
        "",
    ]
    out += [
        "## ECS task",
        "",
        table(["task", "主機", "建立", "啟動", "開始停", "停止", "停止碼", "停止原因"], task_rows),
        "",
    ]
    out += ["## CloudTrail 裡的動作", "", table(["時間", "誰", "API", "對象"], ct_rows), ""]

    md = run / f"{run.name}.md"
    md.write_text("\n".join(out) + "\n")
    total = sum(int((b - a).total_seconds()) for a, b in downs)
    print(f"wrote {md}")
    print(
        f"outages {len(downs)}, total {total}s: "
        + ", ".join(f"{hms(a)}-{hms(b)} {int((b - a).total_seconds())}s" for a, b in downs)
    )


if __name__ == "__main__":
    main()
