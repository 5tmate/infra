package imdsv2

import rego.v1

denials := count([entry | some entry in deny])

checked := count([entry | some entry in applicable])

document(type, name, metadata) := {"resources": [object.union(
	{"type": type, "__name": name},
	metadata,
)]}

instance(metadata) := document("aws:ec2/instance:Instance", "test-instance", metadata)

test_no_metadata_options_is_denied if {
	denials == 1 with input as instance({})
}

test_imdsv1_is_denied if {
	denials == 1 with input as instance({"metadataOptions": {
		"httpEndpoint": "enabled",
		"httpTokens": "optional",
	}})
}

test_imdsv2_is_allowed if {
	denials == 0 with input as instance({"metadataOptions": {
		"httpEndpoint": "enabled",
		"httpTokens": "required",
	}})
}

test_metadata_endpoint_disabled_is_allowed if {
	denials == 0 with input as instance({"metadataOptions": {"httpEndpoint": "disabled"}})
}

test_non_instance_resource_is_ignored if {
	denials == 0 with input as document("aws:ec2/vpc:Vpc", "a-vpc", {})
	checked == 0 with input as document("aws:ec2/vpc:Vpc", "a-vpc", {})
}

test_compliant_instance_is_still_applicable if {
	checked == 1 with input as instance({"metadataOptions": {"httpTokens": "required"}})
}

test_missing_name_still_denies if {
	denials == 1 with input as {"resources": [{"type": "aws:ec2/instance:Instance"}]}
}

test_violation_message_names_the_instance if {
	some entry in deny with input as instance({})
	contains(entry.message, "test-instance")
}

test_launch_template_without_imdsv2_is_denied if {
	denials == 1 with input as document("aws:ec2/launchTemplate:LaunchTemplate", "lt", {})
}

test_spot_instance_request_without_imdsv2_is_denied if {
	denials == 1 with input as document("aws:ec2/spotInstanceRequest:SpotInstanceRequest", "spot", {})
}

test_violation_message_names_the_resource_kind if {
	some entry in deny with input as document("aws:ec2/launchTemplate:LaunchTemplate", "lt", {})
	contains(entry.message, "launch template")
}
