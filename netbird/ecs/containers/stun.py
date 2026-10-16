from .common import health_check

STUN_PROBE = """
wget -q -T 3 -O /dev/null http://127.0.0.1:9000/health && return 0
echo "relay health endpoint failed"
return 1
"""


def stun(*, image, logs):
    return {
        "name": "stun",
        "image": image,
        "essential": False,
        "memoryReservation": 64,
        "environment": [
            {"name": "NB_ENABLE_STUN", "value": "true"},
            {"name": "NB_STUN_PORTS", "value": "3478"},
            {"name": "NB_LISTEN_ADDRESS", "value": "127.0.0.1:33080"},
            {"name": "NB_EXPOSED_ADDRESS", "value": "rel://127.0.0.1:33080"},
            {"name": "NB_AUTH_SECRET", "value": "stun-only"},
            {"name": "NB_LOG_LEVEL", "value": "warn"},
        ],
        "portMappings": [{"containerPort": 3478, "hostPort": 3478, "protocol": "udp"}],
        "healthCheck": health_check(
            "/busybox/sh",
            STUN_PROBE,
            interval=5,
            retries=2,
            start_period=30,
            on_unhealthy="kill -TERM 1",
        ),
        "restartPolicy": {"enabled": True, "restartAttemptPeriod": 300},
        "stopTimeout": 15,
        "logConfiguration": logs("stun"),
    }
