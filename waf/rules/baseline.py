DECODE_LOWER = [
    {"priority": 0, "type": "URL_DECODE"},
    {"priority": 1, "type": "LOWERCASE"},
]


def _managed(name, group, metric):
    return {
        "name": name,
        "override_action": {"none": {}},
        "statement": {
            "managed_rule_group_statement": {"vendor_name": "AWS", "name": group},
        },
        "visibility_config": {
            "sampled_requests_enabled": True,
            "cloudwatch_metrics_enabled": True,
            "metric_name": metric,
        },
    }


_rate_limit = {
    "name": "rate-limit-ip",
    "action": {"block": {}},
    "statement": {
        "rate_based_statement": {
            "limit": 1300,
            "aggregate_key_type": "IP",
            "evaluation_window_sec": 300,
        }
    },
    "visibility_config": {
        "sampled_requests_enabled": True,
        "cloudwatch_metrics_enabled": True,
        "metric_name": "rate-limit-ip",
    },
}


BASELINE_RULES = [
    _rate_limit,
    _managed("AWS-Common", "AWSManagedRulesCommonRuleSet", "aws-common"),
    _managed("AWS-KnownBadInputs", "AWSManagedRulesKnownBadInputsRuleSet", "aws-kbi"),
    _managed("AWS-IpReputation", "AWSManagedRulesAmazonIpReputationList", "aws-ipreputation"),
]
