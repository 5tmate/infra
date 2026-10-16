def signal(*, image, cert_dir, logs):
    return {
        "name": "signal",
        "image": image,
        "essential": True,
        "memoryReservation": 128,
        "environment": [
            {"name": "NB_PORT", "value": "443"},
            {"name": "NB_LOG_LEVEL", "value": "info"},
            {"name": "NB_CERT_FILE", "value": f"{cert_dir}/fullchain.pem"},
            {"name": "NB_CERT_KEY", "value": f"{cert_dir}/privkey.pem"},
        ],
        "portMappings": [{"containerPort": 443, "hostPort": 443, "protocol": "tcp"}],
        "dependsOn": [{"containerName": "certs", "condition": "SUCCESS"}],
        "mountPoints": [
            {"sourceVolume": "certs", "containerPath": "/etc/letsencrypt", "readOnly": True}
        ],
        "logConfiguration": logs("signal"),
    }
