from .baseline import DECODE_LOWER

_nuclei_ja4 = {
    "byte_match_statement": {
        "search_string": "t13d251100_b78ed14e2fd0_ab7e3b40a677",
        "field_to_match": {"ja4_fingerprint": {"fallback_behavior": "NO_MATCH"}},
        "text_transformations": [{"priority": 0, "type": "NONE"}],
        "positional_constraint": "EXACTLY",
    }
}

_post = {
    "byte_match_statement": {
        "search_string": "POST",
        "field_to_match": {"method": {}},
        "text_transformations": [{"priority": 0, "type": "NONE"}],
        "positional_constraint": "EXACTLY",
    }
}

_validate_code = {
    "byte_match_statement": {
        "search_string": "/api/v1/validate/code",
        "field_to_match": {"uri_path": {}},
        "text_transformations": DECODE_LOWER,
        "positional_constraint": "EXACTLY",
    }
}

_build_tmp_flow = {
    "regex_match_statement": {
        "regex_string": r"^/api/v1/build_public_tmp/[^/]+/flow$",
        "field_to_match": {"uri_path": {}},
        "text_transformations": DECODE_LOWER,
    }
}

_rce_path = {"or_statement": {"statements": [_validate_code, _build_tmp_flow]}}
_langflow_rce = {"and_statement": {"statements": [_post, _rce_path]}}

LANGFLOW_RULES = [
    {
        "name": "Block-Nuclei-JA4-and-Langflow-RCE",
        "action": {"block": {}},
        "statement": {"or_statement": {"statements": [_nuclei_ja4, _langflow_rce]}},
        "visibility_config": {
            "sampled_requests_enabled": True,
            "cloudwatch_metrics_enabled": True,
            "metric_name": "BlockNucleiJA4AndLangflowRCE",
        },
    },
]
