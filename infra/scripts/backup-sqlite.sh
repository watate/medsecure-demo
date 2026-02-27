#!/bin/bash
set -e

# Backup SQLite database to S3
# Run via cron: 0 */6 * * * /usr/local/bin/medsecure-backup

DB_PATH="/data/medsecure.db"
BUCKET=$(aws ssm get-parameter --name "/medsecure/prod/S3_BACKUP_BUCKET" --with-decryption --query "Parameter.Value" --output text 2>/dev/null || echo "")
REGION="ap-southeast-1"

if [ -z "$BUCKET" ]; then
  echo "S3_BACKUP_BUCKET not configured in SSM, skipping backup"
  exit 0
fi

if [ ! -f "$DB_PATH" ]; then
  echo "Database file not found at $DB_PATH, skipping backup"
  exit 0
fi

TIMESTAMP=$(date -u +"%Y%m%d-%H%M%S")
S3_KEY="backups/medsecure-${TIMESTAMP}.db"

# Use sqlite3 .backup for a consistent snapshot
BACKUP_PATH="/tmp/medsecure-backup-${TIMESTAMP}.db"
sqlite3 "$DB_PATH" ".backup '$BACKUP_PATH'"

aws s3 cp "$BACKUP_PATH" "s3://${BUCKET}/${S3_KEY}" --region "$REGION"
rm -f "$BACKUP_PATH"

echo "Backed up to s3://${BUCKET}/${S3_KEY}"
