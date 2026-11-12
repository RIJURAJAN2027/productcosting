#!/bin/sh
# Back up the whole database (all organisations and companies) with pg_dump.
#   scripts/backup-db.sh [output-directory]      default: ./backups (older than KEEP_DAYS=30 days are deleted)
# Run from the repository directory on the Docker host; schedule it daily and copy the files off-site.
set -eu
OUT_DIR="${1:-backups}"
mkdir -p "$OUT_DIR"
FILE="$OUT_DIR/costing_$(date +%Y%m%d_%H%M).dump"
docker compose exec -T db pg_dump -U costing -d costing --format=custom --compress=6 > "$FILE"
chmod 600 "$FILE"
echo "Backup written to $FILE ($(du -h "$FILE" | cut -f1))"
# keep KEEP_DAYS days of backups (default 30)
find "$OUT_DIR" -name 'costing_*.dump' -mtime +"${KEEP_DAYS:-30}" -delete
