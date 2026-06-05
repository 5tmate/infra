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
    "langflow-waf-logs",
    bucket="aws-waf-logs-langflowcf",
    tags=tags,
    opts=pulumi.ResourceOptions(protect=True),
)

aws.wafv2.WebAclLoggingConfiguration(
    "langflow-waf-logging",
    resource_arn=langflow_acl.arn,
    log_destination_configs=[log_bucket.arn],
)

pulumi.export("langflow_web_acl_arn", langflow_acl.arn)
