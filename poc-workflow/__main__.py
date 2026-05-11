import json

import pulumi
import pulumi_aws as aws

config = pulumi.Config()
domain = config.require("domain")
fake_version = config.require("version")

dns = pulumi.StackReference("organization/5tmate-dns/prod")
zone_id = dns.get_output("zone_id")
cert_arn = dns.get_output("cert_arn")

tags = {"App": "5tmate"}

# Lambda execution role — CloudWatch Logs only, no AWS data access.
# If a scanner finds a real exploit, the role grants nothing useful.
role = aws.iam.Role(
    "poc-workflow-role",
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
    "poc-workflow-role-basic",
    role=role.name,
    policy_arn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
)

fn = aws.lambda_.Function(
    "poc-workflow",
    runtime="nodejs20.x",
    handler="index.handler",
    role=role.arn,
    code=pulumi.FileArchive("./lambda/src"),
    timeout=10,
    memory_size=256,
    architectures=["arm64"],
    environment={"variables": {"N8N_FAKE_VERSION": fake_version}},
    tags=tags,
)

furl = aws.lambda_.FunctionUrl(
    "poc-workflow-furl",
    function_name=fn.name,
    authorization_type="NONE",
    invoke_mode="BUFFERED",
)

aws.lambda_.Permission(
    "poc-workflow-furl-public",
    action="lambda:InvokeFunctionUrl",
    function=fn.name,
    principal="*",
    function_url_auth_type="NONE",
    statement_id="FunctionURLAllowPublicAccess",
)

# Function URL invocation also requires lambda:InvokeFunction on top of
# lambda:InvokeFunctionUrl — the URL gate passes auth, then Lambda re-checks
# InvokeFunction. The aws_lambda_permission resource can't express the
# Bool condition `lambda:InvokedViaFunctionUrl=true`; the unconditional grant
# is safe here because the function role has no AWS data access.
aws.lambda_.Permission(
    "poc-workflow-furl-invoke",
    action="lambda:InvokeFunction",
    function=fn.name,
    principal="*",
    statement_id="FunctionURLAllowInvokeAction",
)

# Function URL is "https://<id>.lambda-url.<region>.on.aws/" — CloudFront
# origins want the bare host with no scheme or trailing slash.
furl_host = furl.function_url.apply(lambda u: u.removeprefix("https://").rstrip("/"))

# Managed policies:
#   4135ea2d-6df8-44a3-9df3-4b5a84be39ad = Managed-CachingDisabled
#   b689b0a8-53d0-40ab-baf2-68738e2966ac = Managed-AllViewerExceptHostHeader
distribution = aws.cloudfront.Distribution(
    "poc-workflow-distribution",
    enabled=True,
    is_ipv6_enabled=True,
    aliases=[domain],
    origins=[
        {
            "domain_name": furl_host,
            "origin_id": "poc-workflow-furl",
            "custom_origin_config": {
                "http_port": 80,
                "https_port": 443,
                "origin_protocol_policy": "https-only",
                "origin_ssl_protocols": ["TLSv1.2"],
            },
        }
    ],
    default_cache_behavior={
        "target_origin_id": "poc-workflow-furl",
        "viewer_protocol_policy": "redirect-to-https",
        "allowed_methods": ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"],
        "cached_methods": ["GET", "HEAD"],
        "cache_policy_id": "4135ea2d-6df8-44a3-9df3-4b5a84be39ad",
        "origin_request_policy_id": "b689b0a8-53d0-40ab-baf2-68738e2966ac",
    },
    viewer_certificate={
        "acm_certificate_arn": cert_arn,
        "ssl_support_method": "sni-only",
        "minimum_protocol_version": "TLSv1.2_2021",
    },
    restrictions={"geo_restriction": {"restriction_type": "none"}},
    tags=tags,
)

aws.route53.Record(
    "poc-workflow-alias",
    zone_id=zone_id,
    name=domain,
    type="A",
    aliases=[
        {
            "name": distribution.domain_name,
            "zone_id": distribution.hosted_zone_id,
            "evaluate_target_health": False,
        }
    ],
)

pulumi.export("domain", domain)
pulumi.export("fake_version", fake_version)
pulumi.export("function_url", furl.function_url)
pulumi.export("cloudfront_domain", distribution.domain_name)
pulumi.export("distribution_id", distribution.id)
