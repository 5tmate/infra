DATABASES = ["store", "idp", "events"]


def container_definitions(
    *,
    log_group,
    region,
    management_url,
    idp_url,
    config_s3_uri,
    config_sha256,
    litestream_image,
    dashboard_image,
    server_image,
    stun_image,
    aws_cli_image,
):
    def logs(stream):
        return {
            "logDriver": "awslogs",
            "options": {
                "awslogs-group": log_group,
                "awslogs-region": region,
                "awslogs-stream-prefix": stream,
            },
        }

    litestream_mounts = [
        {"sourceVolume": "netbird-data", "containerPath": "/var/lib/netbird"},
        {
            "sourceVolume": "litestream-config",
            "containerPath": "/etc/litestream.yml",
            "readOnly": True,
        },
    ]

    return [
        {
            "name": "dashboard",
            "image": dashboard_image,
            "essential": True,
            "memoryReservation": 128,
            "portMappings": [{"containerPort": 80, "hostPort": 80, "protocol": "tcp"}],
            "environment": [
                {"name": "NETBIRD_MGMT_API_ENDPOINT", "value": management_url},
                {"name": "NETBIRD_MGMT_GRPC_API_ENDPOINT", "value": management_url},
                {"name": "AUTH_AUDIENCE", "value": "netbird-dashboard"},
                {"name": "AUTH_CLIENT_ID", "value": "netbird-dashboard"},
                {"name": "AUTH_CLIENT_SECRET", "value": ""},
                {"name": "AUTH_AUTHORITY", "value": idp_url},
                {"name": "USE_AUTH0", "value": "false"},
                {
                    "name": "AUTH_SUPPORTED_SCOPES",
                    "value": "openid profile email groups",
                },
                {"name": "AUTH_REDIRECT_URI", "value": "/nb-auth"},
                {"name": "AUTH_SILENT_REDIRECT_URI", "value": "/nb-silent-auth"},
                {"name": "NGINX_SSL_PORT", "value": "443"},
                {"name": "LETSENCRYPT_DOMAIN", "value": "none"},
            ],
            "logConfiguration": logs("dashboard"),
        },
        *[
            {
                "name": f"restore-{db}",
                "image": litestream_image,
                "essential": False,
                "memoryReservation": 64,
                "command": [
                    "restore",
                    "-config",
                    "/etc/litestream.yml",
                    "-if-replica-exists",
                    "-force",
                    "-integrity-check",
                    "full",
                    f"/var/lib/netbird/{db}.db",
                ],
                "mountPoints": litestream_mounts,
                "logConfiguration": logs("restore"),
            }
            for db in DATABASES
        ],
        {
            "name": "litestream",
            "image": litestream_image,
            "essential": True,
            "memoryReservation": 128,
            "command": ["replicate", "-config", "/etc/litestream.yml"],
            "stopTimeout": 60,
            "dependsOn": [
                {"containerName": f"restore-{db}", "condition": "SUCCESS"} for db in DATABASES
            ],
            "mountPoints": litestream_mounts,
            "logConfiguration": logs("litestream"),
        },
        {
            "name": "config",
            "image": aws_cli_image,
            "essential": False,
            "memoryReservation": 64,
            "command": ["s3", "cp", config_s3_uri, "/etc/netbird/config.yaml"],
            "environment": [
                {"name": "AWS_REGION", "value": region},
                {"name": "CONFIG_SHA256", "value": config_sha256},
            ],
            "mountPoints": [{"sourceVolume": "netbird-config", "containerPath": "/etc/netbird"}],
            "logConfiguration": logs("config"),
        },
        {
            "name": "netbird-server",
            "image": server_image,
            "essential": True,
            "memoryReservation": 768,
            "command": ["--config", "/etc/netbird/config.yaml"],
            "environment": [{"name": "AWS_REGION", "value": region}],
            "dependsOn": [
                {"containerName": "litestream", "condition": "START"},
                {"containerName": "config", "condition": "SUCCESS"},
            ],
            "portMappings": [{"containerPort": 443, "hostPort": 443, "protocol": "tcp"}],
            "mountPoints": [
                {"sourceVolume": "netbird-data", "containerPath": "/var/lib/netbird"},
                {
                    "sourceVolume": "netbird-config",
                    "containerPath": "/etc/netbird",
                    "readOnly": True,
                },
            ],
            "logConfiguration": logs("server"),
        },
        {
            "name": "stun",
            "image": stun_image,
            "essential": False,
            "memoryReservation": 64,
            "environment": [
                {"name": "NB_ENABLE_STUN", "value": "true"},
                {"name": "NB_STUN_PORTS", "value": "3478"},
                {"name": "NB_LISTEN_ADDRESS", "value": "127.0.0.1:33080"},
                {"name": "NB_EXPOSED_ADDRESS", "value": "rel://127.0.0.1:33080"},
                {"name": "NB_AUTH_SECRET", "value": "stun-only"},
            ],
            "portMappings": [{"containerPort": 3478, "hostPort": 3478, "protocol": "udp"}],
            "logConfiguration": logs("stun"),
        },
    ]
