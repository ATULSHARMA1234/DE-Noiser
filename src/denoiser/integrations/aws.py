"""
AWS CloudWatch integration for fetching log events.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import boto3
from botocore.config import Config

from denoiser.exceptions import IngestionError
from denoiser.ingestion.models import LogRecord
from denoiser.logging import get_logger

logger = get_logger(__name__)

# Bound IMDS credential resolution. When a host has no AWS credentials, boto3's
# default provider chain probes the EC2 metadata endpoint (169.254.169.254) and
# blocks for ~20s of retries before giving up — which would make the connector
# API endpoints hang before returning their 502. Capping the metadata timeout
# and attempts makes an unreachable/credential-less AWS fail in ~a second. Only
# set as defaults so an operator can still override.
os.environ.setdefault("AWS_METADATA_SERVICE_TIMEOUT", "1")
os.environ.setdefault("AWS_METADATA_SERVICE_NUM_ATTEMPTS", "1")

# Fast-fail timeouts for the actual CloudWatch service calls.
_FAST_FAIL_CONFIG = Config(
    connect_timeout=3,
    read_timeout=5,
    retries={"max_attempts": 2, "mode": "standard"},
)


def build_logs_client(region_name: str | None = None):
    """A CloudWatch Logs client configured to fail fast when AWS is unreachable."""
    return boto3.client("logs", region_name=region_name, config=_FAST_FAIL_CONFIG)


class CloudWatchReader:
    """Reads logs from AWS CloudWatch Logs."""

    def __init__(self, region_name: str | None = None) -> None:
        """Initialize the CloudWatch client.

        Parameters
        ----------
        region_name : str | None
            The AWS region. If None, uses default from environment/config.
        """
        try:
            self.client = build_logs_client(region_name)
            logger.debug("Successfully initialized AWS CloudWatch client.")
        except Exception as e:
            raise IngestionError(f"Failed to initialize AWS client. Check your AWS credentials: {e}") from e

    def read(self, log_group: str, log_stream: str | None = None, limit: int = 1000) -> Iterator[LogRecord]:
        """Fetch logs from a CloudWatch log group.

        Parameters
        ----------
        log_group : str
            The name of the log group.
        log_stream : str | None
            The name of the log stream. If None, fetches from all streams in the group.
        limit : int
            The maximum number of log events to fetch.

        Yields
        ------
        LogRecord
            A record for each log event.
        """
        logger.info(f"Fetching logs from CloudWatch {log_group} (stream={log_stream or 'ALL'})...")

        try:
            if log_stream:
                response = self.client.get_log_events(
                    logGroupName=log_group,
                    logStreamName=log_stream,
                    limit=limit
                )
                events = response.get('events', [])
            else:
                # Filter log events across the entire group
                response = self.client.filter_log_events(
                    logGroupName=log_group,
                    limit=limit
                )
                events = response.get('events', [])
        except Exception as e:
            raise IngestionError(f"Failed to fetch CloudWatch logs: {e}") from e

        if not events:
            logger.warning(f"No logs found in CloudWatch group {log_group}.")
            return

        source_name = f"aws://{log_group}"
        if log_stream:
            source_name += f"/{log_stream}"

        for i, event in enumerate(events, 1):
            message = event.get('message', '').strip()
            if not message:
                continue

            yield LogRecord(
                raw_text=message,
                source=source_name,
                line_number=i,
            )
