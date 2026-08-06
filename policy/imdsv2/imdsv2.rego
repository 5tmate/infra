package imdsv2

import rego.v1

# METADATA
# title: EC2 instances must enforce IMDSv2
# custom:
#   message: 'Add metadata_options={"http_tokens": "required"} to the instance.'
deny_imdsv2_required contains msg if {
	input.type == "aws:ec2/instance:Instance"
	not imdsv2_enforced
	msg := sprintf(
		"EC2 instance '%s' must enforce IMDSv2: set metadata_options={\"http_tokens\": \"required\"} (found metadata_options=%v)",
		[input.__name, object.get(input, "metadataOptions", null)],
	)
}

# Allow-list, not deny-list: metadataOptions is absent entirely when unset, and
# an undefined lookup passes silently, so `!= "required"` would miss that case.
imdsv2_enforced if input.metadataOptions.httpTokens == "required"

imdsv2_enforced if input.metadataOptions.httpEndpoint == "disabled"
