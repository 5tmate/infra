package cis_aws

import rego.v1

title := "CIS AWS Foundations — the controls a Pulumi preview can answer"

# Every control gates except the two named below. Each rule already refuses to
# judge a stack that declares none of the resources it watches, so an enabled
# rule with nothing to look at simply reports n/a rather than a false violation.

#
# root_account_keys has no rule at all. AWS does not expose root access keys to
# any API, so a preview can never carry the fact this control needs.
enforced := {
	"alarm_cloudtrail_changes",
	"alarm_cmk_deletion",
	"alarm_config_changes",
	"alarm_console_auth_failures",
	"alarm_console_without_mfa",
	"alarm_gateway_changes",
	"alarm_iam_policy_changes",
	"alarm_nacl_changes",
	"alarm_organizations_changes",
	"alarm_root_usage",
	"alarm_route_table_changes",
	"alarm_s3_policy_changes",
	"alarm_security_group_changes",
	"alarm_unauthorized_api",
	"alarm_vpc_changes",
	"cloudtrail_multi_region",
	"nacl_admin_ports",
	"s3_access_logging",
	"support_role",
	"sg_admin_ports",
}

titles := {
	"s3_access_logging": "S3 buckets must have server access logging enabled",
	"cloudtrail_multi_region": "declared CloudTrail trails must be multi-region and logging",
	"alarm_unauthorized_api": "a log metric filter and alarm must watch unauthorized API calls",
	"alarm_console_without_mfa": "a log metric filter and alarm must watch console sign-in without MFA",
	"alarm_root_usage": "a log metric filter and alarm must watch root account usage",
	"alarm_iam_policy_changes": "a log metric filter and alarm must watch IAM policy changes",
	"alarm_cloudtrail_changes": "a log metric filter and alarm must watch CloudTrail configuration changes",
	"alarm_console_auth_failures": "a log metric filter and alarm must watch console authentication failures",
	"alarm_cmk_deletion": "a log metric filter and alarm must watch CMK disable or scheduled deletion",
	"alarm_s3_policy_changes": "a log metric filter and alarm must watch S3 bucket policy changes",
	"alarm_config_changes": "a log metric filter and alarm must watch AWS Config configuration changes",
	"alarm_security_group_changes": "a log metric filter and alarm must watch security group changes",
	"alarm_nacl_changes": "a log metric filter and alarm must watch network ACL changes",
	"alarm_gateway_changes": "a log metric filter and alarm must watch network gateway changes",
	"alarm_route_table_changes": "a log metric filter and alarm must watch route table changes",
	"alarm_vpc_changes": "a log metric filter and alarm must watch VPC changes",
	"alarm_organizations_changes": "a log metric filter and alarm must watch AWS Organizations changes",
	"support_role": "an IAM role named for support must have AWSSupportAccess attached",
	"root_account_keys": "the root account must not have access keys (not checkable from IaC)",
	"nacl_admin_ports": "network ACLs must not allow 0.0.0.0/0 to administration ports",
	"sg_admin_ports": "security groups must not expose administration ports to 0.0.0.0/0",
}

ADMIN_PORTS := {22: "SSH", 3389: "RDP"}

BUCKET := "aws:s3/bucket:Bucket"

BUCKET_V2 := "aws:s3/bucketV2:BucketV2"

BUCKET_LOGGING := "aws:s3/bucketLoggingV2:BucketLoggingV2"

TRAIL := "aws:cloudtrail/trail:Trail"

METRIC_FILTER := "aws:cloudwatch/logMetricFilter:LogMetricFilter"

METRIC_ALARM := "aws:cloudwatch/metricAlarm:MetricAlarm"

NETWORK_ACL := "aws:ec2/networkAcl:NetworkAcl"

SECURITY_GROUP := "aws:ec2/securityGroup:SecurityGroup"

ROLE := "aws:iam/role:Role"

ROLE_ATTACHMENT := "aws:iam/rolePolicyAttachment:RolePolicyAttachment"

SUPPORT_POLICY := "arn:aws:iam::aws:policy/AWSSupportAccess"

MONITORED := {
	"alarm_unauthorized_api": {"description": "unauthorized API calls", "keywords": {"ERROR", "Denied"}},
	"alarm_console_without_mfa": {"description": "console sign-in without MFA", "keywords": {"ConsoleLogin", "MfaUsed"}},
	"alarm_root_usage": {"description": "root account usage", "keywords": {"root"}},
	"alarm_iam_policy_changes": {"description": "IAM policy changes", "keywords": {"DeleteGroupPolicy", "DeleteRolePolicy"}},
	"alarm_cloudtrail_changes": {"description": "CloudTrail configuration changes", "keywords": {"CreateTrail", "UpdateTrail"}},
	"alarm_console_auth_failures": {"description": "console authentication failures", "keywords": {"ConsoleLogin", "Failed"}},
	"alarm_cmk_deletion": {"description": "CMK disable or scheduled deletion", "keywords": {"DisableKey", "ScheduleKeyDeletion"}},
	"alarm_s3_policy_changes": {"description": "S3 bucket policy changes", "keywords": {"PutBucketAcl", "PutBucketPolicy"}},
	"alarm_config_changes": {"description": "AWS Config configuration changes", "keywords": {"PutConfigurationRecorder", "StopConfigurationRecorder"}},
	"alarm_security_group_changes": {"description": "security group changes", "keywords": {"AuthorizeSecurityGroupIngress", "AuthorizeSecurityGroupEgress"}},
	"alarm_nacl_changes": {"description": "network ACL changes", "keywords": {"CreateNetworkAcl", "CreateNetworkAclEntry"}},
	"alarm_gateway_changes": {"description": "network gateway changes", "keywords": {"CreateCustomerGateway", "DeleteCustomerGateway"}},
	"alarm_route_table_changes": {"description": "route table changes", "keywords": {"CreateRoute", "CreateRouteTable"}},
	"alarm_vpc_changes": {"description": "VPC changes", "keywords": {"CreateVpc", "DeleteVpc"}},
	"alarm_organizations_changes": {"description": "AWS Organizations changes", "keywords": {"AcceptHandshake", "AttachPolicy"}},
}

WATCHED := {
	"s3_access_logging": {BUCKET, BUCKET_V2},
	"cloudtrail_multi_region": {TRAIL},
	"alarm_unauthorized_api": {METRIC_FILTER},
	"alarm_console_without_mfa": {METRIC_FILTER},
	"alarm_root_usage": {METRIC_FILTER},
	"alarm_iam_policy_changes": {METRIC_FILTER},
	"alarm_cloudtrail_changes": {METRIC_FILTER},
	"alarm_console_auth_failures": {METRIC_FILTER},
	"alarm_cmk_deletion": {METRIC_FILTER},
	"alarm_s3_policy_changes": {METRIC_FILTER},
	"alarm_config_changes": {METRIC_FILTER},
	"alarm_security_group_changes": {METRIC_FILTER},
	"alarm_nacl_changes": {METRIC_FILTER},
	"alarm_gateway_changes": {METRIC_FILTER},
	"alarm_route_table_changes": {METRIC_FILTER},
	"alarm_vpc_changes": {METRIC_FILTER},
	"alarm_organizations_changes": {METRIC_FILTER},
	"nacl_admin_ports": {NETWORK_ACL},
	"sg_admin_ports": {SECURITY_GROUP},
}

applicable contains entry if {
	resource := input.resources[_]
	resource.type in WATCHED[control]
	entry := {"control": control, "resource": name(resource)}
}

applicable contains entry if {
	role := support_named_roles[_]
	entry := {"control": "support_role", "resource": name(role)}
}

# s3_access_logging — S3 server access logging
deny contains entry if {
	resource := input.resources[_]
	resource.type == BUCKET
	count(object.get(resource, "loggings", [])) == 0
	entry := finding("s3_access_logging", sprintf(
		"S3 bucket '%s' has no access logging: set loggings=[{...}]",
		[name(resource)],
	))
}

deny contains entry if {
	resource := input.resources[_]
	resource.type == BUCKET_V2
	count(logging_configurations) == 0
	entry := finding("s3_access_logging", sprintf(
		"S3 bucket '%s' has no access logging: declare an aws.s3.BucketLoggingV2 in this stack",
		[name(resource)],
	))
}

# cloudtrail_multi_region — CloudTrail enabled in all regions
deny contains entry if {
	resource := input.resources[_]
	resource.type == TRAIL
	object.get(resource, "isMultiRegionTrail", false) != true
	entry := finding("cloudtrail_multi_region", sprintf(
		"trail '%s' is not multi-region: set is_multi_region_trail=True",
		[name(resource)],
	))
}

deny contains entry if {
	resource := input.resources[_]
	resource.type == TRAIL
	object.get(resource, "enableLogging", true) != true
	entry := finding("cloudtrail_multi_region", sprintf("trail '%s' has logging disabled", [name(resource)]))
}

# alarm_* — only judged when this stack declares any metric filter
deny contains entry if {
	monitoring_declared
	spec := MONITORED[control]
	not watched_by_alarm(spec.keywords)
	entry := finding(control, sprintf(
		"no log metric filter with a matching alarm watches %s (pattern must contain %v)",
		[spec.description, sort([keyword | some keyword in spec.keywords])],
	))
}

monitoring_declared if {
	resource := input.resources[_]
	resource.type == METRIC_FILTER
}

watched_by_alarm(keywords) if {
	filter := input.resources[_]
	filter.type == METRIC_FILTER
	pattern := object.get(filter, "pattern", "")
	every keyword in keywords {
		contains(pattern, keyword)
	}
	alarmed(metric_name(filter))
}

# support_role — only judged for roles this stack names for support
deny contains entry if {
	role := support_named_roles[_]
	not has_support_access(object.get(role, "name", ""))
	entry := finding("support_role", sprintf(
		"IAM role '%s' is named for support but nothing attaches AWSSupportAccess to it in this stack",
		[name(role)],
	))
}

support_named_roles contains role if {
	role := input.resources[_]
	role.type == ROLE
	contains(lower(object.get(role, "name", "")), "support")
}

has_support_access(role_name) if {
	attachment := input.resources[_]
	attachment.type == ROLE_ATTACHMENT
	object.get(attachment, "policyArn", "") == SUPPORT_POLICY
	object.get(attachment, "role", "") == role_name
}

# nacl_admin_ports — network ACLs
deny contains entry if {
	resource := input.resources[_]
	resource.type == NETWORK_ACL
	rule := object.get(resource, "ingress", [])[_]
	object.get(rule, "action", "") == "allow"
	object.get(rule, "cidrBlock", "") == "0.0.0.0/0"
	service := ADMIN_PORTS[port]
	covers(rule, port)
	entry := finding("nacl_admin_ports", sprintf(
		"network ACL '%s' allows %s (port %d) from 0.0.0.0/0",
		[name(resource), service, port],
	))
}

# sg_admin_ports — security groups
deny contains entry if {
	resource := input.resources[_]
	resource.type == SECURITY_GROUP
	rule := object.get(resource, "ingress", [])[_]
	"0.0.0.0/0" in object.get(rule, "cidrBlocks", [])
	service := ADMIN_PORTS[port]
	covers(rule, port)
	entry := finding("sg_admin_ports", sprintf(
		"security group '%s' exposes %s (port %d) to 0.0.0.0/0",
		[name(resource), service, port],
	))
}

name(resource) := object.get(resource, "__name", "<unnamed>")

finding(control, message) := {"control": control, "message": message}

metric_name(resource) := object.get(object.get(resource, "metricTransformation", {}), "name", "")

alarmed(metric) if {
	resource := input.resources[_]
	resource.type == METRIC_ALARM
	object.get(resource, "metricName", "") == metric
}

logging_configurations contains n if {
	resource := input.resources[_]
	resource.type == BUCKET_LOGGING
	n := name(resource)
}

covers(rule, _) if object.get(rule, "protocol", "") == "-1"

covers(rule, port) if {
	object.get(rule, "fromPort", -1) <= port
	object.get(rule, "toPort", -1) >= port
}
