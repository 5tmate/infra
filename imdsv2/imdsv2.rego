package imdsv2

import rego.v1

deny_imdsv2_required contains msg if {
	kind := managed_kinds[input.type]
	not imdsv2_enforced
	msg := sprintf(
		"EC2 %s '%s' must enforce IMDSv2: set metadata_options={\"http_tokens\": \"required\"} (found metadata_options=%v)",
		[kind, input.__name, object.get(input, "metadataOptions", null)],
	)
}

managed_kinds := {
	"aws:ec2/instance:Instance": "instance",
	"aws:ec2/launchTemplate:LaunchTemplate": "launch template",
	"aws:ec2/spotInstanceRequest:SpotInstanceRequest": "spot instance request",
}

imdsv2_enforced if input.metadataOptions.httpTokens == "required"

imdsv2_enforced if input.metadataOptions.httpEndpoint == "disabled"
