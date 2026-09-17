import json

import pulumi
import pulumi_aws as aws
import pulumi_random as random

from .settings import BACKUP_BUCKET, NAME, ROOT, backup_force_destroy, domain, tags

ssm_role = aws.iam.Role(
    "ssm-role",
    assume_role_policy=json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": "sts:AssumeRole",
                    "Principal": {"Service": "ec2.amazonaws.com"},
                }
            ],
        }
    ),
    tags={**tags, "Name": NAME},
)

aws.iam.RolePolicyAttachment(
    "ssm-core",
    role=ssm_role.name,
    policy_arn="arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore",
)

aws.iam.RolePolicyAttachment(
    "ecs-container-instance",
    role=ssm_role.name,
    policy_arn="arn:aws:iam::aws:policy/service-role/AmazonEC2ContainerServiceforEC2Role",
)

instance_profile = aws.iam.InstanceProfile(
    "instance-profile",
    role=ssm_role.name,
    tags={**tags, "Name": NAME},
)


backup_bucket = aws.s3.Bucket(
    "backup",
    bucket=BACKUP_BUCKET,
    force_destroy=backup_force_destroy,
    tags={**tags, "Name": BACKUP_BUCKET, "Purpose": "litestream-backup"},
)

aws.s3.BucketPublicAccessBlock(
    "backup-private",
    bucket=backup_bucket.id,
    block_public_acls=True,
    block_public_policy=True,
    ignore_public_acls=True,
    restrict_public_buckets=True,
)

aws.s3.BucketServerSideEncryptionConfigurationV2(
    "backup-encryption",
    bucket=backup_bucket.id,
    rules=[{"apply_server_side_encryption_by_default": {"sse_algorithm": "AES256"}}],
)

auth_secret = random.RandomBytes(
    "auth-secret", length=32, opts=pulumi.ResourceOptions(protect=True)
)
session_key = random.RandomBytes(
    "session-key", length=32, opts=pulumi.ResourceOptions(protect=True)
)
store_key = random.RandomBytes(
    "store-encryption-key", length=32, opts=pulumi.ResourceOptions(protect=True)
)

_config_template = (ROOT / "files" / "config.yaml").read_text()

netbird_config = aws.s3.BucketObject(
    "config-yaml",
    bucket=backup_bucket.id,
    key="config/config.yaml",
    content=pulumi.Output.all(auth_secret.base64, session_key.base64, store_key.base64).apply(
        lambda v: (
            _config_template.replace("__DOMAIN__", domain)
            .replace("__AUTH_SECRET__", v[0])
            .replace("__SESSION_KEY__", v[1])
            .replace("__ENCRYPTION_KEY__", v[2])
        )
    ),
    opts=pulumi.ResourceOptions(delete_before_replace=True),
)

_traefik_template = (ROOT / "files" / "traefik-dynamic.yml").read_text()

traefik_config = aws.s3.BucketObject(
    "traefik-dynamic",
    bucket=backup_bucket.id,
    key="config/traefik-dynamic.yml",
    content=_traefik_template.replace("__DOMAIN__", domain),
    opts=pulumi.ResourceOptions(delete_before_replace=True),
)

backup_access = aws.iam.RolePolicy(
    "litestream-s3",
    role=ssm_role.name,
    policy=backup_bucket.arn.apply(
        lambda arn: json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
                        "Resource": f"{arn}/*",
                    },
                    {
                        "Effect": "Allow",
                        "Action": ["s3:ListBucket", "s3:GetBucketLocation"],
                        "Resource": arn,
                    },
                ],
            }
        )
    ),
)
