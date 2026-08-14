package imdsv2

import rego.v1

instance(metadata) := object.union(
	{
		"type": "aws:ec2/instance:Instance",
		"__name": "test-instance",
	},
	metadata,
)

test_no_metadata_options_is_denied if {
	violations := deny_imdsv2_required with input as instance({})
	count(violations) == 1
}

test_imdsv1_is_denied if {
	violations := deny_imdsv2_required with input as instance({"metadataOptions": {
		"httpEndpoint": "enabled",
		"httpProtocolIpv6": "disabled",
		"httpTokens": "optional",
	}})
	count(violations) == 1
}

test_imdsv2_is_allowed if {
	violations := deny_imdsv2_required with input as instance({"metadataOptions": {
		"httpEndpoint": "enabled",
		"httpProtocolIpv6": "disabled",
		"httpTokens": "required",
	}})
	count(violations) == 0
}

test_metadata_endpoint_disabled_is_allowed if {
	violations := deny_imdsv2_required with input as instance({"metadataOptions": {
		"httpEndpoint": "disabled",
		"httpProtocolIpv6": "disabled",
	}})
	count(violations) == 0
}

test_non_instance_resource_is_ignored if {
	violations := deny_imdsv2_required with input as {
		"type": "aws:ec2/vpc:Vpc",
		"__name": "a-vpc",
		"cidrBlock": "10.9.0.0/24",
	}
	count(violations) == 0
}

test_violation_message_names_the_instance if {
	violations := deny_imdsv2_required with input as instance({})
	some msg in violations
	contains(msg, "test-instance")
	not contains(msg, "MISSING")
}

resource(type, metadata) := object.union(
	{
		"type": type,
		"__name": "test-resource",
	},
	metadata,
)

test_launch_template_without_imdsv2_is_denied if {
	violations := deny_imdsv2_required with input as resource("aws:ec2/launchTemplate:LaunchTemplate", {})
	count(violations) == 1
}

test_launch_template_with_imdsv2_is_allowed if {
	violations := deny_imdsv2_required with input as resource("aws:ec2/launchTemplate:LaunchTemplate", {"metadataOptions": {"httpTokens": "required"}})
	count(violations) == 0
}

test_spot_instance_request_without_imdsv2_is_denied if {
	violations := deny_imdsv2_required with input as resource("aws:ec2/spotInstanceRequest:SpotInstanceRequest", {})
	count(violations) == 1
}

test_spot_instance_request_with_imdsv2_is_allowed if {
	violations := deny_imdsv2_required with input as resource("aws:ec2/spotInstanceRequest:SpotInstanceRequest", {"metadataOptions": {"httpTokens": "required"}})
	count(violations) == 0
}

test_violation_message_names_the_resource_kind if {
	violations := deny_imdsv2_required with input as resource("aws:ec2/launchTemplate:LaunchTemplate", {})
	some msg in violations
	contains(msg, "launch template")
}
