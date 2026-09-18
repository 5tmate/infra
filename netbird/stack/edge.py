import pulumi
import pulumi_aws as aws

from .service import zone
from .settings import NAME, admin_domain, domain, tags, zone_name

us_east_1 = aws.Provider("us-east-1", region="us-east-1")

cert = aws.acm.get_certificate(
    domain=f"*.{zone_name}",
    statuses=["ISSUED"],
    most_recent=True,
    opts=pulumi.InvokeOptions(provider=us_east_1),
)

caching_disabled = aws.cloudfront.get_cache_policy(name="Managed-CachingDisabled")
forward_all_but_host = aws.cloudfront.get_origin_request_policy(
    name="Managed-AllViewerExceptHostHeader"
)

dashboard_cdn = aws.cloudfront.Distribution(
    "dashboard",
    enabled=True,
    comment=f"{NAME} dashboard",
    aliases=[admin_domain],
    origins=[
        {
            "origin_id": "dashboard",
            "domain_name": domain,
            "custom_origin_config": {
                "http_port": 80,
                "https_port": 443,
                "origin_protocol_policy": "http-only",
                "origin_ssl_protocols": ["TLSv1.2"],
            },
        }
    ],
    default_cache_behavior={
        "target_origin_id": "dashboard",
        "viewer_protocol_policy": "redirect-to-https",
        "allowed_methods": ["GET", "HEAD", "OPTIONS"],
        "cached_methods": ["GET", "HEAD"],
        "cache_policy_id": caching_disabled.id,
        "origin_request_policy_id": forward_all_but_host.id,
    },
    restrictions={"geo_restriction": {"restriction_type": "none"}},
    viewer_certificate={
        "acm_certificate_arn": cert.arn,
        "ssl_support_method": "sni-only",
        "minimum_protocol_version": "TLSv1.2_2021",
    },
    tags={**tags, "Name": NAME},
)

aws.route53.Record(
    "admin",
    zone_id=zone.zone_id,
    name=admin_domain,
    type="A",
    aliases=[
        {
            "name": dashboard_cdn.domain_name,
            "zone_id": dashboard_cdn.hosted_zone_id,
            "evaluate_target_health": False,
        }
    ],
)
