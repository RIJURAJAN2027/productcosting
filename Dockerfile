# syntax=docker/dockerfile:1
# One image: the React UI is built in the first stage and served by the FastAPI backend.
#
# Behind a TLS-inspecting corporate proxy, pass its CA certificate as a build secret (it is used
# only while downloading packages and is not stored in the image):
#   docker build --secret id=ca,src=/path/to/corporate-ca.pem -t productcosting .
# With docker compose, paste it into docker/extra-ca.pem instead.

# ---------------------------------------------------------------- UI build
FROM node:22-alpine AS ui
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json ./
RUN --mount=type=secret,id=ca,required=false \
    if grep -q "BEGIN CERTIFICATE" /run/secrets/ca 2>/dev/null; then export NODE_EXTRA_CA_CERTS=/run/secrets/ca; fi; \
    npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---------------------------------------------------------------- runtime
FROM python:3.13-slim AS app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PC_ENV=production \
    PC_FRONTEND_DIST=/app/static
WORKDIR /app
RUN groupadd --system --gid 10001 costing && useradd --system --uid 10001 --gid costing --home /app costing
COPY backend/requirements.txt ./
RUN --mount=type=secret,id=ca,required=false \
    if grep -q "BEGIN CERTIFICATE" /run/secrets/ca 2>/dev/null; then \
      cat /etc/ssl/certs/ca-certificates.crt /run/secrets/ca > /tmp/build-ca.pem && export PIP_CERT=/tmp/build-ca.pem; \
    fi; \
    pip install -r requirements.txt && rm -f /tmp/build-ca.pem
COPY backend/app ./app
COPY backend/alembic ./alembic
COPY backend/alembic.ini ./
COPY --from=ui /ui/dist ./static
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
# strip Windows line endings in case the files were checked out on Windows
RUN sed -i 's/\r$//' /usr/local/bin/entrypoint.sh && chmod 0755 /usr/local/bin/entrypoint.sh \
    && chown -R costing:costing /app
USER costing
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status == 200 else 1)"
ENTRYPOINT ["entrypoint.sh"]
CMD ["serve"]
