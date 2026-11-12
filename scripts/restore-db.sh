#!/bin/sh
# Restore a backup made by backup-db.sh. REPLACES the current database contents.
#   scripts/restore-db.sh backups/costing_YYYYmmdd_HHMM.dump
set -eu
FILE="${1:?usage: scripts/restore-db.sh <backup.dump>}"
[ -f "$FILE" ] || { echo "No such file: $FILE" >&2; exit 1; }
printf "This replaces ALL data in the database with %s. Type 'restore' to continue: " "$FILE"
read -r answer
[ "$answer" = "restore" ] || { echo "Cancelled."; exit 1; }
docker compose stop app
docker compose exec -T db pg_restore -U costing -d costing --clean --if-exists --no-owner < "$FILE"
docker compose start app
echo "Restored $FILE. The application is starting again."
