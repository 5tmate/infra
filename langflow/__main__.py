import json

import pulumi
import pulumi_aws as aws

fake_version = pulumi.Config().require("version")

tags = {"App": "5tmate"}

# Lambda execution role: CloudWatch Logs only, no AWS data access.
role = aws.iam.Role(
    "langflow-role",
    assume_role_policy=json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"Service": "lambda.amazonaws.com"},
                    "Action": "sts:AssumeRole",
                }
            ],
        }
    ),
    tags=tags,
)

aws.iam.RolePolicyAttachment(
    "langflow-role-basic",
    role=role.name,
    policy_arn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
)

fn = aws.lambda_.Function(
    "langflow",
    runtime="nodejs20.x",
    handler="index.handler",
    role=role.arn,
    code=pulumi.FileArchive("./lambda/src"),
    timeout=10,
    memory_size=256,
    architectures=["arm64"],
    environment={"variables": {"LANGFLOW_FAKE_VERSION": fake_version}},
    tags=tags,
)

furl = aws.lambda_.FunctionUrl(
    "langflow-furl",
    function_name=fn.name,
    authorization_type="NONE",
    invoke_mode="BUFFERED",
)

aws.lambda_.Permission(
    "langflow-furl-public",
    action="lambda:InvokeFunctionUrl",
    function=fn.name,
    principal="*",
    function_url_auth_type="NONE",
    statement_id="FunctionURLAllowPublicAccess",
)

# Function URL invocation also requires lambda:InvokeFunction after URL auth.
# The unconditional grant is safe here because the function role has no AWS data access.
aws.lambda_.Permission(
    "langflow-furl-invoke",
    action="lambda:InvokeFunction",
    function=fn.name,
    principal="*",
    statement_id="FunctionURLAllowInvokeAction",
)

pulumi.export("fake_version", fake_version)
pulumi.export("function_url", furl.function_url)
