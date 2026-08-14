"""Evaluate every rule package against a Pulumi preview.

Each package declares `titles` (control id to description) and emits `applicable`
and `deny` entries tagged with the control they belong to, so a clean run can
distinguish "checked and compliant" from "nothing here to check".

Exit codes: 0 pass, 1 policy violation, 2 the gate itself could not run.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RULES = REPO / "policy" / "rules"

UNKNOWN = "04da6b54-80e4-46f7-96ec-b56ff0331ba9"
MANAGED_TYPES = {
    "aws:ec2/instance:Instance",
    "aws:ec2/launchTemplate:LaunchTemplate",
    "aws:ec2/spotInstanceRequest:SpotInstanceRequest",
}

PASS, VIOLATION, BROKEN = 0, 1, 2


class GateError(Exception):
    pass


def resources_from_preview(preview_file):
    if not preview_file.exists():
        raise GateError(f"{preview_file} not found; the preview did not run")
    document = json.loads(preview_file.read_text())
    if not document.get("steps"):
        raise GateError(f"{preview_file} records no steps; the preview did not run")
    resources = []
    for step in document["steps"]:
        state = step.get("newState") or {}
        inputs = state.get("inputs")
        if inputs is None or not state.get("type"):
            continue
        resources.append(
            {**inputs, "type": state["type"], "__name": state.get("urn", "").rsplit("::", 1)[-1]}
        )
    if not resources:
        raise GateError(f"{preview_file} contains no resource inputs; the gate cannot judge it")
    return resources


def reject_unknowns(label, resources):
    for resource in resources:
        if resource.get("type") not in MANAGED_TYPES:
            continue
        if UNKNOWN in json.dumps(resource.get("metadataOptions")):
            raise GateError(
                f"{label}: '{resource.get('__name')}' has an unresolved value "
                f"inside metadata_options; the gate cannot judge it"
            )


def evaluate(resources):
    rule_files = sorted(p for p in RULES.glob("*.rego") if not p.name.endswith("_test.rego"))
    if not rule_files:
        raise GateError(f"no rule packages found in {RULES}")
    command = ["opa", "eval"]
    for path in rule_files:
        command += ["-d", str(path)]
    command += ["-I", "-f", "json", "data"]

    result = subprocess.run(
        command,
        input=json.dumps({"resources": resources}),
        capture_output=True,
        text=True,
        cwd=REPO,
    )
    if result.returncode != 0:
        raise GateError(f"opa eval failed: {result.stderr.strip()}")
    document = json.loads(result.stdout)["result"][0]["expressions"][0]["value"]

    report = {}
    for package, body in sorted(document.items()):
        if not isinstance(body, dict) or "deny" not in body:
            continue
        titles = body.get("titles", {})
        if not titles:
            raise GateError(f"package '{package}' declares no control titles")
        gating = set(body.get("enforced", []))
        controls = {control: {"applicable": [], "deny": []} for control in titles}
        for entry in body.get("applicable", []):
            controls.setdefault(entry["control"], {"applicable": [], "deny": []})
            controls[entry["control"]]["applicable"].append(entry["resource"])
        for entry in body.get("deny", []):
            controls.setdefault(entry["control"], {"applicable": [], "deny": []})
            controls[entry["control"]]["deny"].append(entry["message"])
        report[package] = {
            "title": body.get("title", package),
            "titles": titles,
            "enforced": gating,
            "controls": controls,
        }
    if not report:
        raise GateError("no rule package exposed a 'deny' rule; the gate cannot judge anything")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("preview", metavar="PREVIEW_JSON")
    parser.add_argument("--label")
    arguments = parser.parse_args()
    preview_file = Path(arguments.preview).resolve()
    label = arguments.label or preview_file.stem
    try:
        resources = resources_from_preview(preview_file)
        reject_unknowns(label, resources)
        report = evaluate(resources)
    except GateError as error:
        print(f"policy gate: {error}", file=sys.stderr)
        return BROKEN

    failed = False
    print(f"{label} — {len(resources)} resources declared")
    for package, entry in report.items():
        print(f"  {package}  {entry['title']}")
        for control in sorted(entry["controls"]):
            found = entry["controls"][control]
            gating = control in entry["enforced"]
            if found["deny"] and gating:
                state = f"FAIL ({len(found['deny'])})"
                failed = True
            elif found["deny"]:
                state = f"off ({len(found['deny'])})"
            elif found["applicable"] and gating:
                state = f"pass ({len(found['applicable'])} checked)"
            elif found["applicable"]:
                state = "off"
            else:
                state = "n/a"
            print(f"    {control:30} {state:18} {entry['titles'].get(control, '')}")
            for message in sorted(found["deny"]):
                print(f"        - {message}")

    return VIOLATION if failed else PASS


if __name__ == "__main__":
    sys.exit(main())
