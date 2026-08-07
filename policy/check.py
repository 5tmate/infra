"""Fail if any stack declares an EC2 resource that does not enforce IMDSv2.

Exit codes: 0 pass, 1 policy violation, 2 the gate itself could not run.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

SELF = Path(__file__).resolve()
REPO = SELF.parent.parent
POLICY = REPO / "policy" / "imdsv2"
QUERY = "data.imdsv2.deny_imdsv2_required with input as input.resources[_]"
STACK = "prod"

INVOKE_RESULTS = {
    "aws:ssm/getParameter:getParameter": {"value": "ami-00000000000000000"},
    "aws:route53/getZone:getZone": {
        "id": "ZDUMMY000000000",
        "zoneId": "ZDUMMY000000000",
        "name": "example.invalid.",
    },
}

UNKNOWN = "__UNKNOWN__"
MANAGED_TYPES = {
    "aws:ec2/instance:Instance",
    "aws:ec2/launchTemplate:LaunchTemplate",
    "aws:ec2/spotInstanceRequest:SpotInstanceRequest",
}

PASS, VIOLATION, BROKEN = 0, 1, 2


class GateError(Exception):
    pass


def stack_config(stack_dir):
    path = stack_dir / f"Pulumi.{STACK}.yaml"
    if not path.exists():
        raise GateError(f"{path} not found; the program needs its config to run")
    import yaml

    document = yaml.safe_load(path.read_text()) or {}
    values = {}
    for key, value in (document.get("config") or {}).items():
        if isinstance(value, dict) and "secure" in value:
            raise GateError(f"{path} encrypts '{key}'; the gate cannot read it")
        values[key] = value if isinstance(value, str) else json.dumps(value)
    return values


def extract(stack_dir):
    import asyncio
    import os
    import runpy

    import pulumi
    import yaml
    from pulumi.runtime.stack import wait_for_rpcs

    project_file = stack_dir / "Pulumi.yaml"
    if not project_file.exists():
        raise GateError(f"{project_file} not found")
    project = yaml.safe_load(project_file.read_text())["name"]
    os.environ["PULUMI_CONFIG"] = json.dumps(stack_config(stack_dir))

    registered = []
    unmocked = []

    class Mocks(pulumi.runtime.Mocks):
        def new_resource(self, args):
            registered.append({**(args.inputs or {}), "type": args.typ, "__name": args.name})
            return [f"{args.name}_id", args.inputs]

        def call(self, args):
            if args.token not in INVOKE_RESULTS:
                unmocked.append(args.token)
                return {}
            return INVOKE_RESULTS[args.token]

    pulumi.runtime.set_mocks(Mocks(), project=project, stack=STACK, preview=True)
    sys.path.insert(0, str(stack_dir))
    os.chdir(stack_dir)

    async def run_program():
        runpy.run_path(str(stack_dir / "__main__.py"), run_name="__main__")
        await wait_for_rpcs()

    try:
        asyncio.get_event_loop().run_until_complete(run_program())
    except BaseException:
        if not unmocked:
            raise
    if unmocked:
        raise GateError(f"no mock for invoke '{unmocked[0]}'; add it to INVOKE_RESULTS") from None
    return registered


def extract_via_subprocess(stack_dir):
    interpreter = stack_dir / ".venv" / "bin" / "python"
    if not interpreter.exists():
        raise GateError(f"{stack_dir.name}: no .venv; run `make sync`")
    reason = "unknown"
    for _ in range(2):
        result = subprocess.run(
            [str(interpreter), str(SELF), "--extract", str(stack_dir)],
            capture_output=True,
            text=True,
            cwd=REPO,
        )
        if result.returncode == PASS:
            try:
                return json.loads(result.stdout)
            except json.JSONDecodeError:
                reason = "extraction printed something that is not JSON"
                continue
        stderr = result.stderr.strip()
        reason = stderr.splitlines()[-1] if stderr else f"exit {result.returncode}"
    raise GateError(f"{stack_dir.name}: extraction failed: {reason}")


def reject_unknowns(stack_dir, resources):
    for resource in resources:
        if resource.get("type") not in MANAGED_TYPES:
            continue
        if UNKNOWN in json.dumps(resource.get("metadataOptions")):
            raise GateError(
                f"{stack_dir.name}: '{resource.get('__name')}' has an unresolved value "
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


def check_every_stack():
    stacks = sorted(path.parent for path in REPO.glob("*/Pulumi.yaml"))
    if not stacks:
        raise GateError("no Pulumi stacks found")
    failures = []
    for stack_dir in stacks:
        resources = extract_via_subprocess(stack_dir)
        reject_unknowns(stack_dir, resources)
        failures += [(stack_dir.name, message) for message in evaluate(resources)]
    if failures:
        for name, message in failures:
            print(f"{name}: {message}")
        return VIOLATION
    print(f"IMDSv2: {len(stacks)} stacks clean")
    return PASS


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extract", metavar="STACK_DIR")
    arguments = parser.parse_args()
    try:
        if arguments.extract:
            resources = extract(Path(arguments.extract).resolve())
            print(json.dumps(resources, default=lambda value: UNKNOWN))
            return PASS
        return check_every_stack()
    except GateError as error:
        prefix = "" if arguments.extract else "imdsv2 gate: "
        print(f"{prefix}{error}", file=sys.stderr)
        return BROKEN


if __name__ == "__main__":
    sys.exit(main())
