import pulumi

from stack import service  # noqa: F401
from stack.failover import alerts, event_log, failover
from stack.machine import asg, eip
from stack.network import sg, vpc
from stack.settings import domain
from stack.storage import backup_bucket

pulumi.export("asg_name", asg.name)
pulumi.export("public_ip", eip.public_ip)
pulumi.export("domain", domain)
pulumi.export("dashboard_url", f"https://{domain}")
pulumi.export("litestream_bucket", backup_bucket.bucket)
pulumi.export("vpc_id", vpc.id)
pulumi.export("security_group_id", sg.id)
pulumi.export("alerts_topic_arn", alerts.arn)
pulumi.export("failover_function", failover.name)
pulumi.export("asg_event_log_group", event_log.name)
