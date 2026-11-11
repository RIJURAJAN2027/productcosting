"""Application settings (environment variables prefixed with PC_)."""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PC_", env_file=".env", extra="ignore")

    env: str = "development"                       # development | test | production
    database_url: str = "postgresql+psycopg://costing:costing@localhost:5432/costing"
    secret_key: str = "change-me-in-production-please-use-a-long-random-string"
    access_token_minutes: int = 12 * 60
    cors_origins: str = "http://localhost:5173,http://localhost:8080"
    max_upload_mb: int = 50
    # first start: create this tenant/admin when the database is empty
    bootstrap_tenant: str = "Default"
    bootstrap_admin_email: str = "admin@example.com"
    bootstrap_admin_password: str = "ChangeMe!123"
    load_demo_company: bool = True
    # SSO (OpenID Connect) – optional
    oidc_issuer: str | None = None
    oidc_client_id: str | None = None
    oidc_client_secret: str | None = None
    oidc_redirect_uri: str | None = None
    frontend_dist: str | None = None               # serve the built SPA from the API container

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
