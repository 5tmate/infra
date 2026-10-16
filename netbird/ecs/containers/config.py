def config(*, image, config_s3_uri, config_sha256, region, logs):
    return {
        "name": "config",
        "image": image,
        "essential": False,
        "memoryReservation": 64,
        "command": ["s3", "cp", config_s3_uri, "/etc/netbird/config.yaml"],
        "environment": [
            {"name": "AWS_REGION", "value": region},
            {"name": "CONFIG_SHA256", "value": config_sha256},
        ],
        "mountPoints": [{"sourceVolume": "netbird-config", "containerPath": "/etc/netbird"}],
        "logConfiguration": logs("config"),
    }
