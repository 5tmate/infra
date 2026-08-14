"""Fail if a Pulumi preview declares an EC2 resource that does not enforce IMDSv2.

Exit codes: 0 pass, 1 policy violation, 2 the gate itself could not run.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
POLICY = REPO / "imdsv2"
QUERY = "data.imdsv2.deny_imdsv2_required with input as input.resources[_]"

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
    result = subprocess.run(
        ["opa", "eval", "-d", str(POLICY), "-I", "-f", "json", QUERY],
        input=json.dumps({"resources": resources}),
        capture_output=True,
        text=True,
        cwd=REPO,
    )
    if result.returncode != 0:
        raise GateError(f"opa eval failed: {result.stderr.strip()}")
    document = json.loads(result.stdout)
    return [
        message
        for entry in document.get("result", [])
        for expression in entry["expressions"]
        for message in expression["value"]
    ]


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
        failures = evaluate(resources)
        if failures:
            for message in failures:
                print(f"{label}: {message}")
            return VIOLATION
        print(f"IMDSv2: {label} clean ({len(resources)} resources)")
        return PASS
    except GateError as error:
        print(f"imdsv2 gate: {error}", file=sys.stderr)
        return BROKEN


if __name__ == "__main__":
    sys.exit(main())
