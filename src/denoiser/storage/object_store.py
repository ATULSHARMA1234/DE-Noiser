import os

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from denoiser.logging import get_logger

logger = get_logger(__name__)

class ObjectStore:
    def __init__(
        self,
        endpoint_url: str | None = None,
        access_key: str | None = None,
        secret_key: str | None = None,
        bucket_name: str | None = None,
    ):
        # Default to the local MinIO settings if not provided in env
        self.endpoint_url = endpoint_url or os.getenv("S3_ENDPOINT", "http://localhost:9000")
        self.access_key = access_key or os.getenv("S3_ACCESS_KEY", "admin")
        self.secret_key = secret_key or os.getenv("S3_SECRET_KEY", "password123")
        self.bucket_name = bucket_name or os.getenv("S3_BUCKET", "semanticos-logs")

        self.client = boto3.client(
            's3',
            endpoint_url=self.endpoint_url,
            aws_access_key_id=self.access_key,
            aws_secret_access_key=self.secret_key,
            # MinIO requires addressing style set to path
            config=Config(signature_version='s3v4', s3={"addressing_style": "path"}),
            region_name='us-east-1' # dummy region
        )

        self._ensure_bucket_exists()

    def _ensure_bucket_exists(self):
        try:
            self.client.head_bucket(Bucket=self.bucket_name)
        except ClientError as e:
            error_code = e.response['Error']['Code']
            if error_code == '404':
                logger.info(f"Bucket {self.bucket_name} does not exist. Creating it...")
                self.client.create_bucket(Bucket=self.bucket_name)
            else:
                logger.error(f"Failed to check/create bucket {self.bucket_name}: {e}")

    def upload_file(self, file_path: str, object_name: str = None) -> bool:
        """Upload a file to an S3 bucket"""
        if object_name is None:
            object_name = os.path.basename(file_path)

        try:
            logger.info(f"Uploading {file_path} to s3://{self.bucket_name}/{object_name}...")
            self.client.upload_file(file_path, self.bucket_name, object_name)
            return True
        except Exception as e:
            logger.error(f"Failed to upload {file_path} to S3: {e}")
            return False

    def list_objects(self, prefix: str = "") -> list:
        """List objects in the bucket, optionally filtering by prefix."""
        try:
            response = self.client.list_objects_v2(Bucket=self.bucket_name, Prefix=prefix)
            if 'Contents' in response:
                return [obj['Key'] for obj in response['Contents']]
            return []
        except Exception as e:
            logger.error(f"Failed to list objects in S3: {e}")
            return []

    def download_file(self, object_name: str, file_path: str) -> bool:
        """Download a file from an S3 bucket"""
        try:
            self.client.download_file(self.bucket_name, object_name, file_path)
            return True
        except Exception as e:
            logger.error(f"Failed to download {object_name} from S3: {e}")
            return False

    def delete_object(self, object_name: str) -> bool:
        try:
            self.client.delete_object(Bucket=self.bucket_name, Key=object_name)
            return True
        except Exception as e:
            logger.error(f"Failed to delete {object_name} from S3: {e}")
            return False
