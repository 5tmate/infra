import json
import pulumi
import pulumi_aws as aws
from rules import BASELINE_RULES, LANGFLOW_RULES

tags = {"App": "5tmate"}


def _numbered(rules):
    return [{**r, "priority": i} for i, r in enumerate(rules)]


langflow_acl = aws.wafv2.WebAcl(
    "langflow-waf",
    name="langflow-cf",
    scope="CLOUDFRONT",
    default_action={"allow": {}},
    rules=_numbered([*LANGFLOW_RULES, *BASELINE_RULES]),
    visibility_config={
        "sampled_requests_enabled": True,
        "cloudwatch_metrics_enabled": True,
        "metric_name": "langflow-cf",
    },
    tags=tags,
)

log_bucket = aws.s3.BucketV2(
    "waf-logs",
    bucket="aws-waf-logs-5tmate",
    tags=tags,
    opts=pulumi.ResourceOptions(protect=True),
)

account_id = aws.get_caller_identity().account_id

bucket_policy = aws.s3.BucketPolicy(
    "waf-logs-policy",
    bucket=log_bucket.id,
    policy=log_bucket.arn.apply(
        lambda arn: json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Sid": "AWSLogDeliveryWrite",
                        "Effect": "Allow",
                        "Principal": {"Service": "delivery.logs.amazonaws.com"},
                        "Action": "s3:PutObject",
                        "Resource": f"{arn}/AWSLogs/{account_id}/*",
                        "Condition": {
                            "StringEquals": {
                                "s3:x-amz-acl": "bucket-owner-full-control",
                                "aws:SourceAccount": account_id,
                            },
                        },
                    },
                    {
                        "Sid": "AWSLogDeliveryAclCheck",
                        "Effect": "Allow",
                        "Principal": {"Service": "delivery.logs.amazonaws.com"},
                        "Action": "s3:GetBucketAcl",
                        "Resource": arn,
                        "Condition": {
                            "StringEquals": {"aws:SourceAccount": account_id},
                        },
                    },
                ],
            }
        )
    ),
)

aws.wafv2.WebAclLoggingConfiguration(
    "langflow-waf-logging",
    resource_arn=langflow_acl.arn,
    log_destination_configs=[log_bucket.arn],
    opts=pulumi.ResourceOptions(depends_on=[bucket_policy]),
)

pulumi.export("langflow_web_acl_arn", langflow_acl.arn)
