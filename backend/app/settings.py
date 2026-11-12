"""Application settings (environment variables prefixed with PC_)."""
from __future__ import annotations

import os
from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


DEV_SECRET = "change-me-in-production-please-use-a-long-random-string"
DEV_ADMIN_PASSWORD = "ChangeMe!123"


DATABASE_ENV = ("PC_DATABASE_URL", "DATABASE_URL", "POSTGRES_URL")


def normalise_database_url(url: str) -> str:
    """Accept the URL forms that hosting providers hand out (postgres://, postgresql://) and use the
    psycopg 3 driver for them."""
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def on_vercel() -> bool:
    return bool(os.environ.get("VERCEL"))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PC_", env_file=".env", extra="ignore")

    env: str = "development"                       # development | test | production
    database_url: str = "postgresql+psycopg://costing:costing@localhost:5432/costing"
    secret_key: str = DEV_SECRET
    access_token_minutes: int = 12 * 60
    cors_origins: str = "http://localhost:5173,http://localhost:8080"
    max_upload_mb: int = 50
    # first start: create this tenant/admin when the database is empty
    bootstrap_tenant: str = "Default"
    bootstrap_admin_email: str = "admin@example.com"
    bootstrap_admin_password: str = DEV_ADMIN_PASSWORD
    load_demo_company: bool = True
    # migrate the database schema when the application starts (default: on serverless platforms,
    # where there is no start-up script; Docker runs `python -m app.cli init` instead)
    auto_migrate: bool | None = None
    # serverless platform (Vercel): no connection pool kept between requests
    serverless: bool | None = None
    # SSO (OpenID Connect) – optional
    oidc_issuer: str | None = None
    oidc_client_id: str | None = None
    oidc_client_secret: str | None = None
    oidc_redirect_uri: str | None = None
    frontend_dist: str | None = None               # serve the built SPA from the API container

    @model_validator(mode="after")
    def _platform_defaults(self) -> "Settings":
        # hosted PostgreSQL (e.g. Neon through the Vercel marketplace) publishes DATABASE_URL / POSTGRES_URL
        if "PC_DATABASE_URL" not in os.environ:
            for name in ("DATABASE_URL", "POSTGRES_URL"):
                if os.environ.get(name):
                    self.database_url = os.environ[name]
                    break
        self.database_url = normalise_database_url(self.database_url)
        if on_vercel():
            if "PC_ENV" not in os.environ:
                self.env = "production"        # a deployment is production unless told otherwise
            if self.serverless is None:
                self.serverless = True
            if self.auto_migrate is None:
                self.auto_migrate = True
        self.serverless = bool(self.serverless)
        self.auto_migrate = bool(self.auto_migrate)
        return self

    def check_production(self) -> None:
        """Refuse to start a production server with the well-known development secrets."""
        if self.env != "production":
            return
        problems = []
        if self.secret_key == DEV_SECRET or len(self.secret_key) < 32:
            problems.append("PC_SECRET_KEY must be set to a random string of at least 32 characters")
        if self.bootstrap_admin_password == DEV_ADMIN_PASSWORD:
            problems.append("PC_BOOTSTRAP_ADMIN_PASSWORD must be changed from the default")
        if self.serverless and (self.is_sqlite or not any(os.environ.get(n) for n in DATABASE_ENV)):
            problems.append("connect a PostgreSQL database (DATABASE_URL), e.g. Neon from the Vercel marketplace")
        if problems:
            raise RuntimeError("Unsafe production configuration: " + "; ".join(problems))

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
