import json
from collections.abc import Sequence


def assume_role(service: str) -> str:
    return json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": "sts:AssumeRole",
                    "Principal": {"Service": service},
                }
            ],
        }
    )


def bucket_access(arn: str) -> str:
    return json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
                    "Resource": f"{arn}/*",
                },
                {
                    "Effect": "Allow",
                    "Action": ["s3:ListBucket", "s3:GetBucketLocation"],
                    "Resource": arn,
                },
            ],
        }
    )


def service_update(service_arn: str) -> str:
    return json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": ["ecs:DescribeServices", "ecs:UpdateService"],
                    "Resource": service_arn,
                }
            ],
        }
    )


def dns01_challenge(zone_id: str, domains: Sequence[str]) -> str:
    return json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": ["route53:ListHostedZones", "route53:ListHostedZonesByName"],
                    "Resource": "*",
                },
                {
                    "Effect": "Allow",
                    "Action": "route53:GetChange",
                    "Resource": "arn:aws:route53:::change/*",
                },
                {
                    "Effect": "Allow",
                    "Action": "route53:ListResourceRecordSets",
                    "Resource": f"arn:aws:route53:::hostedzone/{zone_id}",
                },
                {
                    "Effect": "Allow",
                    "Action": "route53:ChangeResourceRecordSets",
                    "Resource": f"arn:aws:route53:::hostedzone/{zone_id}",
                    "Condition": {
                        "ForAllValues:StringEquals": {
                            "route53:ChangeResourceRecordSetsNormalizedRecordNames": [
                                f"_acme-challenge.{domain}" for domain in domains
                            ],
                            "route53:ChangeResourceRecordSetsRecordTypes": ["TXT"],
                        }
                    },
                },
            ],
        }
    )
