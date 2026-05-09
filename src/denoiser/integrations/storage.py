"""
Cloud storage integration for syncing baselines.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import boto3

from denoiser.logging import get_logger

logger = get_logger(__name__)


class S3Storage:
    """Handles syncing baseline indices to/from AWS S3."""

    def __init__(self, bucket_name: str | None = None) -> None:
        self.bucket = bucket_name or os.environ.get("SLD_S3_BUCKET")
        self.client = boto3.client("s3")

    def push(self, local_path: Path, remote_key: str) -> bool:
        """Upload a baseline directory (as a zip) to S3."""
        if not self.bucket:
            logger.error("S3 bucket not configured. Set SLD_S3_BUCKET.")
            return False

        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            logger.info(f"Zipping baseline {local_path}...")
            # make_archive returns the path to the created zip file
            zip_file_path = shutil.make_archive(tmp_path.replace(".zip", ""), "zip", local_path)
            
            logger.info(f"Uploading to s3://{self.bucket}/{remote_key}.zip...")
            self.client.upload_file(zip_file_path, self.bucket, f"{remote_key}.zip")
            # Cleanup the specific zip file created by make_archive
            if os.path.exists(zip_file_path):
                os.remove(zip_file_path)
            return True
        except Exception as e:
            logger.error(f"S3 Upload failed: {e}")
            return False
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    def pull(self, remote_key: str, local_path: Path) -> bool:
        """Download and extract a baseline zip from S3."""
        if not self.bucket:
            logger.error("S3 bucket not configured. Set SLD_S3_BUCKET.")
            return False

        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            logger.info(f"Downloading s3://{self.bucket}/{remote_key}.zip...")
            self.client.download_file(self.bucket, f"{remote_key}.zip", tmp_path)
            
            if local_path.exists():
                shutil.rmtree(local_path)
            local_path.mkdir(parents=True)
            
            logger.info(f"Extracting to {local_path}...")
            shutil.unpack_archive(tmp_path, local_path, "zip")
            return True
        except Exception as e:
            logger.error(f"S3 Download failed: {e}")
            return False
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
