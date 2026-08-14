package cis_aws

import rego.v1

denials(control) := count([entry | some entry in deny; entry.control == control])

checked(control) := count([entry | some entry in applicable; entry.control == control])

document(resources) := {"resources": resources}

resource(type, name, fields) := object.union({"type": type, "__name": name}, fields)

# 2.1.1
test_legacy_bucket_without_logging_is_denied if {
	denials("s3_access_logging") == 1 with input as document([resource(BUCKET, "legacy", {})])
}

test_legacy_bucket_with_logging_is_allowed if {
	denials("s3_access_logging") == 0 with input as document([resource(
		BUCKET, "legacy",
		{"loggings": [{"targetBucket": "logs"}]},
	)])
}

test_modern_bucket_without_a_logging_resource_is_denied if {
	denials("s3_access_logging") == 1 with input as document([resource(BUCKET_V2, "modern", {})])
}

test_modern_bucket_with_a_logging_resource_is_allowed if {
	denials("s3_access_logging") == 0 with input as document([
		resource(BUCKET_V2, "modern", {}),
		resource(BUCKET_LOGGING, "modern-logging", {"targetBucket": "logs"}),
	])
}

test_no_bucket_is_not_applicable if {
	checked("s3_access_logging") == 0 with input as document([resource("aws:ec2/vpc:Vpc", "v", {})])
}

# 3.1
test_single_region_trail_is_denied if {
	denials("cloudtrail_multi_region") == 1 with input as document([resource(
		TRAIL, "trail",
		{"isMultiRegionTrail": false, "enableLogging": true},
	)])
}

test_trail_with_logging_disabled_is_denied if {
	denials("cloudtrail_multi_region") == 1 with input as document([resource(
		TRAIL, "trail",
		{"isMultiRegionTrail": true, "enableLogging": false},
	)])
}

test_compliant_trail_is_allowed if {
	denials("cloudtrail_multi_region") == 0 with input as document([resource(
		TRAIL, "trail",
		{"isMultiRegionTrail": true, "enableLogging": true},
	)])
}

# CloudTrail monitoring alarms — only judged when the stack declares a metric filter
unauthorized_filter := resource(METRIC_FILTER, "unauthorized", {
	"pattern": "{($.errorCode=\"*ERROR*\") || ($.errorCode=\"AccessDenied*\")}",
	"metricTransformation": {"name": "UnauthorizedAPICalls"},
})

matching_alarm := resource(METRIC_ALARM, "alarm", {"metricName": "UnauthorizedAPICalls"})

test_stack_without_any_filter_stays_silent if {
	denials("alarm_unauthorized_api") == 0 with input as document([resource("aws:ec2/vpc:Vpc", "v", {})])
	denials("alarm_root_usage") == 0 with input as document([resource("aws:ec2/vpc:Vpc", "v", {})])
	checked("alarm_unauthorized_api") == 0 with input as document([resource("aws:ec2/vpc:Vpc", "v", {})])
}

test_matching_filter_and_alarm_satisfies_its_control if {
	denials("alarm_unauthorized_api") == 0 with input as document([unauthorized_filter, matching_alarm])
}

test_declaring_monitoring_makes_the_other_controls_apply if {
	denials("alarm_root_usage") == 1 with input as document([unauthorized_filter, matching_alarm])
	checked("alarm_root_usage") == 1 with input as document([unauthorized_filter, matching_alarm])
}

test_filter_without_an_alarm_does_not_satisfy_its_control if {
	denials("alarm_unauthorized_api") == 1 with input as document([unauthorized_filter])
}

test_alarm_watching_another_metric_does_not_count if {
	denials("alarm_unauthorized_api") == 1 with input as document([
		unauthorized_filter,
		resource(METRIC_ALARM, "alarm", {"metricName": "SomethingElse"}),
	])
}

test_filter_missing_a_keyword_does_not_satisfy_its_control if {
	denials("alarm_unauthorized_api") == 1 with input as document([
		resource(METRIC_FILTER, "partial", {
			"pattern": "{$.errorCode=\"*ERROR*\"}",
			"metricTransformation": {"name": "Partial"},
		}),
		resource(METRIC_ALARM, "alarm", {"metricName": "Partial"}),
	])
}

# 5.1
test_nacl_allowing_ssh_from_the_world_is_denied if {
	denials("nacl_admin_ports") == 1 with input as document([resource(NETWORK_ACL, "nacl", {"ingress": [{
		"action": "allow", "protocol": "tcp",
		"cidrBlock": "0.0.0.0/0", "fromPort": 22, "toPort": 22,
	}]})])
}

test_nacl_deny_entry_is_ignored if {
	denials("nacl_admin_ports") == 0 with input as document([resource(NETWORK_ACL, "nacl", {"ingress": [{
		"action": "deny", "protocol": "tcp",
		"cidrBlock": "0.0.0.0/0", "fromPort": 22, "toPort": 22,
	}]})])
}

test_nacl_all_protocols_covers_both_ports if {
	denials("nacl_admin_ports") == 2 with input as document([resource(NETWORK_ACL, "nacl", {"ingress": [{
		"action": "allow", "protocol": "-1",
		"cidrBlock": "0.0.0.0/0", "fromPort": 0, "toPort": 0,
	}]})])
}

# 5.2
test_security_group_open_to_the_world_is_denied if {
	denials("sg_admin_ports") == 1 with input as document([resource(SECURITY_GROUP, "sg", {"ingress": [{
		"protocol": "tcp", "fromPort": 22, "toPort": 22,
		"cidrBlocks": ["0.0.0.0/0"],
	}]})])
}

test_security_group_from_one_address_is_allowed if {
	denials("sg_admin_ports") == 0 with input as document([resource(SECURITY_GROUP, "sg", {"ingress": [{
		"protocol": "tcp", "fromPort": 22, "toPort": 22,
		"cidrBlocks": ["1.163.232.110/32"],
	}]})])
}

test_security_group_port_range_covering_ssh_is_denied if {
	denials("sg_admin_ports") == 1 with input as document([resource(SECURITY_GROUP, "sg", {"ingress": [{
		"protocol": "tcp", "fromPort": 1, "toPort": 1024,
		"cidrBlocks": ["0.0.0.0/0"],
	}]})])
}

test_security_group_unrelated_port_is_allowed if {
	denials("sg_admin_ports") == 0 with input as document([resource(SECURITY_GROUP, "sg", {"ingress": [{
		"protocol": "tcp", "fromPort": 443, "toPort": 443,
		"cidrBlocks": ["0.0.0.0/0"],
	}]})])
}

test_egress_only_group_is_applicable_but_clean if {
	checked("sg_admin_ports") == 1 with input as document([resource(SECURITY_GROUP, "sg", {})])
	denials("sg_admin_ports") == 0 with input as document([resource(SECURITY_GROUP, "sg", {})])
}

# every control stays silent when the stack declares nothing it watches
test_unrelated_stack_reports_nothing if {
	count(deny) == 0 with input as document([resource("aws:route53/zone:Zone", "z", {})])
	count(applicable) == 0 with input as document([resource("aws:route53/zone:Zone", "z", {})])
}
