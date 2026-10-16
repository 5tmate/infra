from .common import make_logs
from .config import config
from .dashboard import dashboard
from .litestream import replicate, restores
from .server import SKIP_HOLD_FILE, server
from .stun import stun

__all__ = ["SKIP_HOLD_FILE", "container_definitions"]


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
