from .common import health_check

DASHBOARD_HEALTH_SERVER = (
    "server { listen 127.0.0.1:8080; access_log off; root /usr/share/nginx/html; }"
)
DASHBOARD_PROBE = "curl -m 3 -fsS -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/"


def dashboard(*, image, management_url, idp_url, logs):
    return {
        "name": "dashboard",
        "image": image,
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
        "entryPoint": ["sh", "-c"],
        "command": [
            f"echo '{DASHBOARD_HEALTH_SERVER}' > /etc/nginx/http.d/health.conf"
            " && exec /usr/bin/supervisord -c /etc/supervisord.conf"
        ],
        "healthCheck": health_check("sh", DASHBOARD_PROBE, interval=5, retries=2, start_period=30),
        "stopTimeout": 15,
        "logConfiguration": logs("dashboard"),
    }
