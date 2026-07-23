import json

import pulumi
import pulumi_aws as aws

tags = {"App": "5tmate"}

role = aws.iam.Role(
    "loganalytics-role",
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
    "loganalytics-role-basic",
    role=role.name,
    policy_arn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
)

log_bucket_arn = "arn:aws:s3:::5tmate-langflow-logs"

aws.iam.RolePolicy(
    "loganalytics-s3-read",
    role=role.name,
    policy=json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": ["s3:GetObject", "s3:ListBucket"],
                    "Resource": [log_bucket_arn, f"{log_bucket_arn}/*"],
                }
            ],
        }
    ),
)


fn = aws.lambda_.Function(
    "loganalytics",
    runtime="python3.12",
    handler="handler.handler",
    role=role.arn,
    code=pulumi.FileArchive("./lambda/src"),
    tags=tags,
)

pulumi.export("function_name", fn.name)
pulumi.export("function_arn", fn.arn)
