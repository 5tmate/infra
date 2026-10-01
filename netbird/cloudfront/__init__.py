from collections.abc import Sequence

import pulumi
import pulumi_aws as aws

ACCESS_LOG_FIELDS = [
    "date",
    "time",
    "x-edge-location",
    "c-ip",
    "cs-method",
    "x-host-header",
    "cs-uri-stem",
    "cs-protocol",
    "cs-protocol-version",
    "cache-behavior-path-pattern",
    "sc-status",
    "x-edge-result-type",
    "x-edge-response-result-type",
    "x-edge-detailed-result-type",
    "time-taken",
    "time-to-first-byte",
    "origin-fbl",
    "origin-lbl",
    "cs-bytes",
    "sc-bytes",
    "sc-content-type",
    "cs(User-Agent)",
    "x-edge-request-id",
]

READ_METHODS = ["GET", "HEAD", "OPTIONS"]
ALL_METHODS = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]


def _create_access_logs(
    name: str,
    *,
    parent: pulumi.Resource,
    provider: aws.Provider,
    resource_name: str,
    distribution_arn: pulumi.Input[str],
) -> None:
    opts = pulumi.ResourceOptions(parent=parent, provider=provider)

    log_group = aws.cloudwatch.LogGroup(
        f"{name}-logs",
        name=f"/aws/vendedlogs/cloudfront/{resource_name}",
        retention_in_days=7,
        tags={"Name": resource_name},
        opts=opts,
    )
    source = aws.cloudwatch.LogDeliverySource(
        f"{name}-log-source",
        name=f"{resource_name}-cloudfront",
        log_type="ACCESS_LOGS",
        resource_arn=distribution_arn,
        opts=opts,
    )
    destination = aws.cloudwatch.LogDeliveryDestination(
        f"{name}-log-destination",
        name=f"{resource_name}-cloudfront",
        output_format="json",
        delivery_destination_configuration={"destination_resource_arn": log_group.arn},
        opts=opts,
    )
    aws.cloudwatch.LogDelivery(
        f"{name}-log-delivery",
        delivery_source_name=source.name,
        delivery_destination_arn=destination.arn,
        record_fields=ACCESS_LOG_FIELDS,
        opts=opts,
    )


class Cdn(pulumi.ComponentResource):
    def __init__(
        self,
        name: str,
        *,
        resource_name: str,
        us_east_1_provider: aws.Provider,
        certificate_arn: str,
        alias: str,
        origin_domain: pulumi.Input[str],
        origin_protocol_policy: str,
        origin_port: int,
        origin_ssl_protocols: Sequence[str],
        allowed_methods: Sequence[str],
        response_headers_policy: str | None,
        grpc_enabled: bool,
        opts: pulumi.ResourceOptions | None = None,
    ):
        super().__init__("netbird:cloudfront:Cdn", name, None, opts)

        caching_disabled = aws.cloudfront.get_cache_policy(name="Managed-CachingDisabled")
        forward_all = aws.cloudfront.get_origin_request_policy(name="Managed-AllViewer")

        behavior = {
            "target_origin_id": name,
            "viewer_protocol_policy": "redirect-to-https",
            "allowed_methods": list(allowed_methods),
            "cached_methods": ["GET", "HEAD"],
            "cache_policy_id": caching_disabled.id,
            "origin_request_policy_id": forward_all.id,
        }
        if grpc_enabled:
            behavior["grpc_config"] = {"enabled": True}
        if response_headers_policy:
            behavior["response_headers_policy_id"] = aws.cloudfront.get_response_headers_policy(
                name=response_headers_policy
            ).id

        self.distribution = aws.cloudfront.Distribution(
            f"{name}-distribution",
            enabled=True,
            comment=alias,
            aliases=[alias],
            origins=[
                {
                    "origin_id": name,
                    "domain_name": origin_domain,
                    "custom_origin_config": {
                        "http_port": origin_port,
                        "https_port": origin_port,
                        "origin_protocol_policy": origin_protocol_policy,
                        "origin_ssl_protocols": list(origin_ssl_protocols),
                    },
                }
            ],
            default_cache_behavior=behavior,
            restrictions={"geo_restriction": {"restriction_type": "none"}},
            viewer_certificate={
                "acm_certificate_arn": certificate_arn,
                "ssl_support_method": "sni-only",
                "minimum_protocol_version": "TLSv1.2_2021",
            },
            tags={"Name": resource_name},
            opts=pulumi.ResourceOptions(parent=self),
        )

        _create_access_logs(
            name,
            parent=self,
            provider=us_east_1_provider,
            resource_name=resource_name,
            distribution_arn=self.distribution.arn,
        )

        self.domain_name = self.distribution.domain_name
        self.hosted_zone_id = self.distribution.hosted_zone_id
        self.register_outputs({"domain_name": self.domain_name})
