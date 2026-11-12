"""Application settings (environment variables prefixed with PC_)."""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


DEV_SECRET = "change-me-in-production-please-use-a-long-random-string"
DEV_ADMIN_PASSWORD = "ChangeMe!123"


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
    # SSO (OpenID Connect) – optional
    oidc_issuer: str | None = None
    oidc_client_id: str | None = None
    oidc_client_secret: str | None = None
    oidc_redirect_uri: str | None = None
    frontend_dist: str | None = None               # serve the built SPA from the API container

    def check_production(self) -> None:
        """Refuse to start a production server with the well-known development secrets."""
        if self.env != "production":
            return
        problems = []
        if self.secret_key == DEV_SECRET or len(self.secret_key) < 32:
            problems.append("PC_SECRET_KEY must be set to a random string of at least 32 characters")
        if self.bootstrap_admin_password == DEV_ADMIN_PASSWORD:
            problems.append("PC_BOOTSTRAP_ADMIN_PASSWORD must be changed from the default")
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
