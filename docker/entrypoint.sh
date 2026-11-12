#!/bin/sh
# Container entry point.
#   serve (default)  migrate the database, create the first admin if needed, start the web server
#   cli ...          run an administration command, e.g. `cli set-password admin@example.com`
#   anything else    run it as a command
set -e

case "${1:-serve}" in
  serve)
    python -m app.cli init
    exec uvicorn app.main:app --host 0.0.0.0 --port 8000 \
      --workers "${WEB_CONCURRENCY:-2}" --proxy-headers --forwarded-allow-ips="${FORWARDED_ALLOW_IPS:-*}" \
      --timeout-keep-alive 30
    ;;
  cli)
    shift
    exec python -m app.cli "$@"
    ;;
  *)
    exec "$@"
    ;;
esac
