#!/usr/bin/env bash
# Update a running installation to the code in this folder – used by the GitHub "Deploy" workflow after a
# merge into main, and usable by hand:   sudo scripts/deploy.sh [revision]
#
#   1. backs up the database (scripts/backup-db.sh) unless SKIP_BACKUP=1,
#   2. rebuilds and restarts the application with Docker (database migrations run on start),
#   3. waits until it answers, then removes old images.
#
# It never asks questions and never touches .env, the database volume or the backups folder.
# The first installation is done with scripts/install-server.sh.
set -euo pipefail
cd "$(dirname "$0")/.."
REVISION="${1:-}"

if [[ ! -f .env ]]; then
  echo "No .env here – install first with: sudo scripts/install-server.sh" >&2
  exit 1
fi

echo "== Deploying ${REVISION:-current files} in $(pwd)"

if [[ "${SKIP_BACKUP:-0}" != 1 ]] && docker compose ps --status running --services 2>/dev/null | grep -qx db; then
  echo "== Database backup before the update"
  scripts/backup-db.sh
fi

echo "== Building and starting"
docker compose up -d --build

echo "== Waiting for the application"
for _ in $(seq 1 120); do
  if docker compose exec -T app python -c \
      "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)" \
      >/dev/null 2>&1; then
    docker image prune -f >/dev/null 2>&1 || true
    [[ -n "${REVISION}" ]] && echo "${REVISION} $(date -u +%Y-%m-%dT%H:%M:%SZ)" > .deployed-revision
    echo "== Deployed ${REVISION:-} – the application is running"
    exit 0
  fi
  sleep 2
done
echo "The application did not start after the update. See: docker compose logs app" >&2
docker compose logs --tail 80 app >&2 || true
exit 1
