#!/bin/bash
# Daily database backup to Backblaze B2 (or any S3-compatible storage)
# Run via cron: 0 2 * * * /opt/anchor/infrastructure/scripts/backup.sh

set -euo pipefail

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="/tmp/anchor_backup_$TIMESTAMP.sql.gz"
BUCKET="${BACKUP_BUCKET:-your-b2-bucket-name}"

echo "==> Backing up database..."

# Dump and compress
docker compose exec -T db pg_dump -U anchor anchor | gzip > "$BACKUP_FILE"

# Upload to B2/S3 (requires aws CLI or b2 CLI configured)
if command -v aws &>/dev/null; then
  aws s3 cp "$BACKUP_FILE" "s3://$BUCKET/backups/$(basename $BACKUP_FILE)"
  echo "==> Uploaded to S3: $BUCKET"
elif command -v b2 &>/dev/null; then
  b2 upload-file "$BUCKET" "$BACKUP_FILE" "backups/$(basename $BACKUP_FILE)"
  echo "==> Uploaded to B2: $BUCKET"
else
  echo "WARNING: No upload CLI found. Backup saved locally: $BACKUP_FILE"
fi

# Keep only last 7 local backups
find /tmp -name "anchor_backup_*.sql.gz" -mtime +7 -delete

echo "==> Backup complete: $BACKUP_FILE"
