import pulumi
import pulumi_aws as aws
import pulumi_command as command 

config = pulumi.Config()
version = config.require("version")
dns = pulumi.StackReference("organization/5tmate-dns/prod")
zone_id = dns.get_output("zone_id")
cert_arn = dns.get_output("cert_arn")
tags = {"App": "5tmate"}


# S3 bucket
bucket = aws.s3.BucketV2(
    "landing",
    force_destroy=True,
    tags=tags,
)

# CloudFront OAC
oac = aws.cloudfront.OriginAccessControl(
    "landing-oac",
    description="OAC for landing S3 bucket",
    origin_access_control_origin_type="s3",
    signing_behavior="always",
    signing_protocol="sigv4",
)

# CloudFront distribution
distribution = aws.cloudfront.Distribution(
    "landing-distribution",
    enabled=True,
    default_root_object="index.html",
    aliases=["www.5tmate.threatreveal.org"],
    origins=[
        {
            "domain_name": bucket.bucket_regional_domain_name,
            "origin_id": "landing-s3",
            "origin_access_control_id": oac.id,
        }
    ],
    default_cache_behavior={
        "target_origin_id": "landing-s3",
        "viewer_protocol_policy": "redirect-to-https",
        "allowed_methods": ["GET", "HEAD"],
        "cached_methods": ["GET", "HEAD"],
        "cache_policy_id": "658327ea-f89d-4fab-a63d-7e88639e58f6",
    },
    viewer_certificate={
        "acm_certificate_arn": cert_arn,
        "ssl_support_method": "sni-only",
    },
    restrictions={"geo_restriction": {"restriction_type": "none"}},
    tags=tags,
)

bucket_policy = aws.s3.BucketPolicy(
    "landing-bucket-policy",
    bucket=bucket.id,
    policy=pulumi.Output.json_dumps({
        "Version": "2012-10-17",
        "Statement": [{
            "Sid": "AllowCloudFrontServicePrincipal",
            "Effect": "Allow",
            "Principal": {"Service": "cloudfront.amazonaws.com"},
            "Action": "s3:GetObject",
            "Resource": bucket.arn.apply(lambda arn: f"{arn}/*"),
            "Condition": {
                "StringEquals": {"AWS:SourceArn": distribution.arn}
            },
        }],
    }),
)

www_record = aws.route53.Record(
    "www-alias",
    zone_id=zone_id,
    name="www.5tmate.threatreveal.org",
    type="A",
    aliases=[{
        "name": distribution.domain_name,
        "zone_id": distribution.hosted_zone_id,
        "evaluate_target_health": False,
    }],
)

# download release

deploy_cmd = command.local.Command(
    "deploy-release",
    create=pulumi.Output.all(
        bucket_name=bucket.bucket,
        distribution_id=distribution.id,
    ).apply(
        lambda args: f"""set -euo pipefail
TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT

URL="https://github.com/5tmate/landing/releases/download/{version}/dist-{version}.zip"
curl -fsSL -o "$TMPDIR/dist.zip" "$URL"
unzip -q "$TMPDIR/dist.zip" -d "$TMPDIR"

# Hashed bundles under /assets/ are immutable for a year.
aws s3 sync "$TMPDIR/dist/assets" "s3://{args['bucket_name']}/assets" \\
  --delete \\
  --cache-control 'public, max-age=31536000, immutable'

# Everything else (index.html, robots.txt, sitemap.xml, favicon, og.png, icons.svg)
# must revalidate so deploys are picked up immediately.
aws s3 sync "$TMPDIR/dist" "s3://{args['bucket_name']}/" \\
  --delete \\
  --exclude 'assets/*' \\
  --cache-control 'public, max-age=0, must-revalidate'

aws cloudfront create-invalidation \\
  --distribution-id "{args['distribution_id']}" \\
  --paths '/*'
"""
    ),
    interpreter=["/bin/bash", "-c"],
    triggers=[version],
)



pulumi.export("deployed_version", version)
pulumi.export("bucket_name", bucket.bucket)
pulumi.export("distribution_domain", distribution.domain_name)
pulumi.export("distribution_id", distribution.id)
