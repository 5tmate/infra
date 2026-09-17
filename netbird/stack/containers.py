from .settings import LITESTREAM_IMAGE, NAME, domain, region, zone_name


def log_config(stream):
    return {
        "logDriver": "awslogs",
        "options": {
            "awslogs-group": f"/ecs/{NAME}",
            "awslogs-region": region,
            "awslogs-stream-prefix": stream,
        },
    }


containers = [
    {
        "name": "traefik",
        "image": "traefik:v3.6",
        "essential": True,
        "memoryReservation": 128,
        "command": [
            "--log.level=INFO",
            "--accesslog=true",
            "--providers.file.filename=/etc/traefik/dynamic.yml",
            "--entrypoints.web.address=:80",
            "--entrypoints.websecure.address=:443",
            "--entrypoints.websecure.allowACMEByPass=true",
            "--entrypoints.websecure.transport.respondingTimeouts.readTimeout=0",
            "--entrypoints.websecure.transport.respondingTimeouts.writeTimeout=0",
            "--entrypoints.websecure.transport.respondingTimeouts.idleTimeout=0",
            "--entrypoints.web.http.redirections.entrypoint.to=websecure",
            "--entrypoints.web.http.redirections.entrypoint.scheme=https",
            f"--certificatesresolvers.letsencrypt.acme.email=admin@{zone_name}",
            "--certificatesresolvers.letsencrypt.acme.storage=/letsencrypt/acme.json",
            "--certificatesresolvers.letsencrypt.acme.tlschallenge=true",
            "--serverstransport.forwardingtimeouts.responseheadertimeout=0s",
            "--serverstransport.forwardingtimeouts.idleconntimeout=0s",
        ],
        "portMappings": [
            {"containerPort": 80, "hostPort": 80, "protocol": "tcp"},
            {"containerPort": 443, "hostPort": 443, "protocol": "tcp"},
        ],
        "mountPoints": [
            {"sourceVolume": "letsencrypt", "containerPath": "/letsencrypt"},
            {
                "sourceVolume": "traefik-dynamic",
                "containerPath": "/etc/traefik/dynamic.yml",
                "readOnly": True,
            },
        ],
        "links": ["dashboard", "netbird-server"],
        "dependsOn": [
            {"containerName": "dashboard", "condition": "START"},
            {"containerName": "netbird-server", "condition": "START"},
        ],
        "logConfiguration": log_config("traefik"),
    },
    {
        "name": "dashboard",
        "image": "netbirdio/dashboard:latest",
        "essential": True,
        "memoryReservation": 128,
        "environment": [
            {"name": "NETBIRD_MGMT_API_ENDPOINT", "value": f"https://{domain}"},
            {
                "name": "NETBIRD_MGMT_GRPC_API_ENDPOINT",
                "value": f"https://{domain}",
            },
            {"name": "AUTH_AUDIENCE", "value": "netbird-dashboard"},
            {"name": "AUTH_CLIENT_ID", "value": "netbird-dashboard"},
            {"name": "AUTH_CLIENT_SECRET", "value": ""},
            {"name": "AUTH_AUTHORITY", "value": f"https://{domain}/oauth2"},
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
        "logConfiguration": log_config("dashboard"),
    },
    {
        "name": "litestream",
        "image": LITESTREAM_IMAGE,
        "essential": True,
        "memoryReservation": 128,
        "command": ["replicate", "-config", "/etc/litestream.yml"],
        "stopTimeout": 60,
        "mountPoints": [
            {"sourceVolume": "netbird-data", "containerPath": "/var/lib/netbird"},
            {
                "sourceVolume": "litestream-config",
                "containerPath": "/etc/litestream.yml",
                "readOnly": True,
            },
        ],
        "logConfiguration": log_config("litestream"),
    },
    {
        "name": "netbird-server",
        "image": "netbirdio/netbird-server:latest",
        "essential": True,
        "memoryReservation": 768,
        "command": ["--config", "/etc/netbird/config.yaml"],
        "dependsOn": [{"containerName": "litestream", "condition": "START"}],
        "portMappings": [
            {"containerPort": 3478, "hostPort": 3478, "protocol": "udp"},
        ],
        "mountPoints": [
            {"sourceVolume": "netbird-data", "containerPath": "/var/lib/netbird"},
            {
                "sourceVolume": "netbird-config",
                "containerPath": "/etc/netbird/config.yaml",
                "readOnly": True,
            },
        ],
        "logConfiguration": log_config("server"),
    },
]
