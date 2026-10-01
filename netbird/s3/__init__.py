import hashlib
from pathlib import Path

import pulumi
import pulumi_aws as aws

CONFIG_TEMPLATE = (Path(__file__).parent / "config.yaml").read_text()


class PrivateBucket(pulumi.ComponentResource):
    def __init__(
        self,
        name: str,
        *,
        bucket: str,
        adopt: bool,
        tags: dict[str, str],
        opts: pulumi.ResourceOptions | None = None,
    ):
        super().__init__("netbird:s3:PrivateBucket", name, None, opts)
        kept = pulumi.ResourceOptions(parent=self, retain_on_delete=True)

        self.bucket = aws.s3.Bucket(
            name,
            bucket=bucket,
            tags=tags,
            opts=pulumi.ResourceOptions(
                parent=self, retain_on_delete=True, import_=bucket if adopt else None
            ),
        )
        aws.s3.BucketPublicAccessBlock(
            f"{name}-private",
            bucket=self.bucket.id,
            block_public_acls=True,
            block_public_policy=True,
            ignore_public_acls=True,
            restrict_public_buckets=True,
            opts=kept,
        )
        aws.s3.BucketServerSideEncryptionConfigurationV2(
            f"{name}-encryption",
            bucket=self.bucket.id,
            rules=[{"apply_server_side_encryption_by_default": {"sse_algorithm": "AES256"}}],
            opts=kept,
        )

        self.name = self.bucket.bucket
        self.arn = self.bucket.arn
        self.register_outputs({"name": self.name, "arn": self.arn})


def _render(values: dict[str, str]) -> str:
    text = CONFIG_TEMPLATE
    for placeholder, value in values.items():
        text = text.replace(placeholder, value)
    return text


class NetBirdConfig(pulumi.ComponentResource):
    def __init__(
        self,
        name: str,
        *,
        bucket: pulumi.Input[str],
        key: str,
        management_domain: str,
        relay_domain: str,
        dashboard_domain: str,
        exposed_address: str,
        issuer: str,
        letsencrypt_enabled: bool,
        auth_secret: pulumi.Input[str],
        session_key: pulumi.Input[str],
        store_encryption_key: pulumi.Input[str],
        owner_email: pulumi.Input[str],
        owner_password_hash: pulumi.Input[str],
        acme_email: pulumi.Input[str],
        opts: pulumi.ResourceOptions | None = None,
    ):
        super().__init__("netbird:s3:NetBirdConfig", name, None, opts)

        content = pulumi.Output.all(
            auth_secret,
            session_key,
            store_encryption_key,
            owner_email,
            owner_password_hash,
            acme_email,
        ).apply(
            lambda v: _render(
                {
                    "__MANAGEMENT_DOMAIN__": management_domain,
                    "__RELAY_DOMAIN__": relay_domain,
                    "__DASHBOARD_DOMAIN__": dashboard_domain,
                    "__EXPOSED_ADDRESS__": exposed_address,
                    "__ISSUER__": issuer,
                    "__LETSENCRYPT_ENABLED__": "true" if letsencrypt_enabled else "false",
                    "__AUTH_SECRET__": v[0],
                    "__SESSION_KEY__": v[1],
                    "__ENCRYPTION_KEY__": v[2],
                    "__OWNER_EMAIL__": v[3],
                    "__OWNER_HASH__": v[4],
                    "__ACME_EMAIL__": v[5],
                }
            )
        )

        config_object = aws.s3.BucketObject(
            "config-yaml",
            bucket=bucket,
            key=key,
            content=content,
            opts=pulumi.ResourceOptions(parent=self, delete_before_replace=True),
        )

        self.s3_uri = pulumi.Output.concat("s3://", config_object.bucket, "/", config_object.key)
        self.content_sha256 = pulumi.Output.unsecret(
            content.apply(lambda text: hashlib.sha256(text.encode()).hexdigest())
        )
        self.register_outputs({"s3_uri": self.s3_uri, "content_sha256": self.content_sha256})
