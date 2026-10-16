def certs(*, image, region, email, domains, logs):
    return {
        "name": "certs",
        "image": image,
        "essential": False,
        "memoryReservation": 128,
        "environment": [{"name": "AWS_REGION", "value": region}],
        "command": [
            "certonly",
            "--dns-route53",
            "--non-interactive",
            "--agree-tos",
            "--keep-until-expiring",
            "--cert-name",
            "relays",
            "-m",
            email,
            *[arg for domain in domains for arg in ("-d", domain)],
        ],
        "mountPoints": [{"sourceVolume": "certs", "containerPath": "/etc/letsencrypt"}],
        "logConfiguration": logs("certs"),
    }
