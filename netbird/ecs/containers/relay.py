def relay(*, image, domain, auth_secret_arn, cert_dir, logs, enable_stun):
    environment = [
        {"name": "NB_LOG_LEVEL", "value": "info"},
        {"name": "NB_LISTEN_ADDRESS", "value": ":8443"},
        {"name": "NB_EXPOSED_ADDRESS", "value": f"rels://{domain}:8443"},
        {"name": "NB_TLS_CERT_FILE", "value": f"{cert_dir}/fullchain.pem"},
        {"name": "NB_TLS_KEY_FILE", "value": f"{cert_dir}/privkey.pem"},
    ]
    port_mappings = [
        {"containerPort": 8443, "hostPort": 8443, "protocol": "tcp"},
        {"containerPort": 8443, "hostPort": 8443, "protocol": "udp"},
    ]
    if enable_stun:
        environment += [
            {"name": "NB_ENABLE_STUN", "value": "true"},
            {"name": "NB_STUN_PORTS", "value": "3478"},
        ]
        port_mappings.append({"containerPort": 3478, "hostPort": 3478, "protocol": "udp"})
    return {
        "name": "relay",
        "image": image,
        "essential": True,
        "memoryReservation": 128,
        "environment": environment,
        "secrets": [{"name": "NB_AUTH_SECRET", "valueFrom": auth_secret_arn}],
        "portMappings": port_mappings,
        "dependsOn": [{"containerName": "certs", "condition": "SUCCESS"}],
        "mountPoints": [
            {"sourceVolume": "certs", "containerPath": "/etc/letsencrypt", "readOnly": True}
        ],
        "logConfiguration": logs("relay"),
    }
