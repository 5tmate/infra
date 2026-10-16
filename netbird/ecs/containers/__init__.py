from .certs import certs
from .common import make_logs
from .config import config
from .dashboard import dashboard
from .litestream import replicate, restores
from .relay import relay
from .server import SKIP_HOLD_FILE, server
from .signal import signal
from .stun import stun

__all__ = ["SKIP_HOLD_FILE", "container_definitions", "relays_container_definitions"]


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
    server_name,
):
    logs = make_logs(log_group, region)
    return [
        dashboard(image=dashboard_image, management_url=management_url, idp_url=idp_url, logs=logs),
        *restores(image=litestream_image, logs=logs),
        replicate(image=litestream_image, logs=logs),
        config(
            image=aws_cli_image,
            config_s3_uri=config_s3_uri,
            config_sha256=config_sha256,
            region=region,
            logs=logs,
        ),
        server(image=server_image, server_name=server_name, region=region, logs=logs),
        stun(image=stun_image, logs=logs),
    ]


def relays_container_definitions(
    *,
    log_group,
    region,
    certs_image,
    signal_image,
    relay_image,
    signal_domain,
    relay_domain,
    auth_secret_arn,
    acme_email,
):
    logs = make_logs(log_group, region)
    cert_dir = "/etc/letsencrypt/live/relays"
    return [
        certs(
            image=certs_image,
            region=region,
            email=acme_email,
            domains=[signal_domain, relay_domain],
            logs=logs,
        ),
        signal(image=signal_image, cert_dir=cert_dir, logs=logs),
        relay(
            image=relay_image,
            domain=relay_domain,
            auth_secret_arn=auth_secret_arn,
            cert_dir=cert_dir,
            logs=logs,
        ),
    ]
