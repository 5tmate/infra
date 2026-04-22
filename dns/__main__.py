import pulumi
import pulumi_aws as aws

config = pulumi.Config()
subdomain = config.require("subdomain")
parent_domain = config.require("parent_domain")
zone_name = f"{subdomain}.{parent_domain}"

tags = {"App": "5tmate", "ManagedBy": "pulumi"}

# Create the public hosted zone for the subdomain
zone = aws.route53.Zone(
    "zone",
    name=zone_name,
    comment=f"Public hosted zone for {zone_name}",
    tags=tags,
)

# Look up the existing parent hosted zone
parent_zone = aws.route53.get_zone(name=parent_domain, private_zone=False)

# Delegate the subdomain by adding NS records in the parent zone
ns_delegation = aws.route53.Record(
    "ns-delegation",
    zone_id=parent_zone.zone_id,
    name=zone_name,
    type="NS",
    ttl=300,
    records=zone.name_servers,
)

# Wildcard certificate (us-east-1 required by CloudFront)
cert = aws.acm.Certificate(
    "wildcard-cert",
    domain_name=f"*.{zone_name}",
    validation_method="DNS",
    tags=tags,
)

# DNS validation record in the zone
validation_opt = cert.domain_validation_options[0]
cert_validation_record = aws.route53.Record(
    "cert-validation-record",
    zone_id=zone.zone_id,
    name=validation_opt.resource_record_name,
    type=validation_opt.resource_record_type,
    ttl=60,
    records=[validation_opt.resource_record_value],
)

# Wait for the certificate to be validated
cert_validation = aws.acm.CertificateValidation(
    "cert-validation",
    certificate_arn=cert.arn,
    validation_record_fqdns=[cert_validation_record.fqdn],
)

pulumi.export("zone_id", zone.zone_id)
pulumi.export("name_servers", zone.name_servers)
pulumi.export("cert_arn", cert_validation.certificate_arn)
