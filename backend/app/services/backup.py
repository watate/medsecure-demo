import logging
import os
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

from app.config import settings

logger = logging.getLogger(__name__)


def backup_to_s3() -> str | None:
    """Backup SQLite database to S3."""
    if not settings.s3_backup_bucket:
        logger.warning("S3_BACKUP_BUCKET not configured, skipping backup")
        return None

    if not os.path.exists(settings.database_path):
        logger.warning("Database file not found at %s", settings.database_path)
        return None

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    s3_key = f"backups/medsecure-{timestamp}.db"

    try:
        s3 = boto3.client("s3", region_name=settings.aws_region)
        s3.upload_file(settings.database_path, settings.s3_backup_bucket, s3_key)
        logger.info("Backed up database to s3://%s/%s", settings.s3_backup_bucket, s3_key)
        return s3_key
    except ClientError:
        logger.exception("Failed to backup database to S3")
        return None
