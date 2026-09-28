"""AWS clients fail fast when the backend/credentials are unreachable, instead
of blocking ~20s on EC2 metadata-endpoint retries."""

import os


def test_logs_client_has_fast_timeouts():
    from denoiser.integrations.aws import build_logs_client
    cfg = build_logs_client(region_name="us-east-1").meta.config
    assert cfg.connect_timeout == 3
    assert cfg.read_timeout == 5


def test_imds_timeout_bounded():
    import denoiser.integrations.aws  # noqa: F401  (import sets the defaults)
    assert os.environ.get("AWS_METADATA_SERVICE_TIMEOUT") == "1"
    assert os.environ.get("AWS_METADATA_SERVICE_NUM_ATTEMPTS") == "1"
