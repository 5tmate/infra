package imdsv2

import rego.v1

title := "IMDSv2 — EC2 metadata service hardening"

titles := {"imdsv2_required": "EC2 instances must require IMDSv2"}

enforced := {"imdsv2_required"}

managed_kinds := {
	"aws:ec2/instance:Instance": "instance",
	"aws:ec2/launchTemplate:LaunchTemplate": "launch template",
	"aws:ec2/spotInstanceRequest:SpotInstanceRequest": "spot instance request",
}

applicable contains entry if {
	resource := input.resources[_]
	managed_kinds[resource.type]
	entry := {"control": "imdsv2_required", "resource": name(resource)}
}

deny contains entry if {
	resource := input.resources[_]
	kind := managed_kinds[resource.type]
	not v2_required(resource)
	entry := {"control": "imdsv2_required", "message": sprintf(
		"EC2 %s '%s' must enforce IMDSv2: set metadata_options={\"http_tokens\": \"required\"} (found metadata_options=%v)",
		[kind, name(resource), object.get(resource, "metadataOptions", null)],
	)}
}

name(resource) := object.get(resource, "__name", "<unnamed>")

v2_required(resource) if resource.metadataOptions.httpTokens == "required"

v2_required(resource) if resource.metadataOptions.httpEndpoint == "disabled"
