# Contributing

## Prerequisites

- [Pulumi CLI](https://www.pulumi.com/docs/install/) v3+
- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- AWS CLI v2 with access to the target account
- [pre-commit](https://pre-commit.com/)
- [OPA](https://www.openpolicyagent.org/docs/latest/#running-opa)

## Hooks

```bash
pre-commit install
```

This installs the IMDSv2 gate. It runs every Pulumi program under mock providers
and rejects a commit that declares an EC2 instance, launch template, or spot
instance request without `metadata_options={"http_tokens": "required"}`. It needs
no AWS credentials.

It is the only thing enforcing IMDSv2 — a clone that skips this step is
unprotected, and `git commit --no-verify` bypasses it.

## AWS Profile Setup

Before running any Pulumi command, log in to AWS:

```bash
aws login --profile <your-profile>
```

Add the following to `~/.aws/config`, replacing `<your-profile>` with your AWS profile name. The `pulumi` profile uses `credential_process` so Pulumi's backend (AWS SDK v1) can resolve credentials correctly.

```ini
[profile pulumi]
credential_process = aws configure export-credentials --profile <your-profile> --format json
region = ap-northeast-1
```

## Deploying Pulumi Stacks Locally

Navigate into the stack directory (`dns`, `landing`, etc.), then log in to that stack's backend path. Replace `<stack>` with the directory name and `<state-bucket>` with the S3 bucket name for this account:

```bash
cd <stack>
export AWS_PROFILE=pulumi
source ../scripts/get-passphrase.sh <stack>
pulumi stack select prod
pulumi up
```

The script fetches the backend URL and passphrase from SSM, logs in to the Pulumi backend, and exports `PULUMI_CONFIG_PASSPHRASE` into your shell. It will error if any SSM parameter does not exist.

## Tear Down

```bash
pulumi destroy
```
