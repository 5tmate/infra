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


output_bucket = aws.s3.BucketV2("loganalytics-output", bucket="5tmate-loganalytics", tags=tags)

aws.s3.BucketPublicAccessBlock(
    "loganalytics-output-pab",
    bucket=output_bucket.id,
    block_public_acls=True,
    block_public_policy=True,
    ignore_public_acls=True,
    restrict_public_buckets=True,
)

aws.iam.RolePolicy(
    "loganalytics-s3-write",
    role=role.name,
    policy=output_bucket.arn.apply(
        lambda arn: json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Action": "s3:PutObject",
                        "Resource": f"{arn}/*",
                    }
                ],
            }
        )
    ),
)

fn = aws.lambda_.Function(
    "loganalytics",
    runtime="python3.12",
    handler="handler.handler",
    role=role.arn,
    code=pulumi.FileArchive("./lambda/build"),
    memory_size=512,
    timeout=120,
    environment={"variables": {"OUTPUT_BUCKET": output_bucket.bucket}},
    tags=tags,
)

schedule = aws.cloudwatch.EventRule(
    "loganalytics-schedule",
    schedule_expression="cron(5 * * * ? *)",
    tags=tags,
)

aws.lambda_.Permission(
    "loganalytics-schedule-invoke",
    action="lambda:InvokeFunction",
    function=fn.name,
    principal="events.amazonaws.com",
    source_arn=schedule.arn,
)

aws.cloudwatch.EventTarget(
    "loganalytics-schedule-target",
    rule=schedule.name,
    arn=fn.arn,
    input="{}",
)

pulumi.export("function_name", fn.name)
pulumi.export("function_arn", fn.arn)
pulumi.export("output_bucket", output_bucket.bucket)
