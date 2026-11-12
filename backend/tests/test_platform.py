"""Hosting platform behaviour: database URLs from providers, serverless (Vercel) defaults,
initialisation on the first request, and stateless single-sign-on state."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.settings import DEV_ADMIN_PASSWORD, Settings, normalise_database_url

STRONG = "x" * 40


def test_provider_database_urls_use_psycopg():
    assert normalise_database_url("postgres://u:p@h/db?sslmode=require") == "postgresql+psycopg://u:p@h/db?sslmode=require"
    assert normalise_database_url("postgresql://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
    assert normalise_database_url("postgresql+psycopg://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
    assert normalise_database_url("sqlite:///x.db") == "sqlite:///x.db"


def test_database_url_from_hosting_provider(monkeypatch):
    monkeypatch.delenv("PC_DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgres://user:pw@db.example.net/costing?sslmode=require")
    assert Settings().database_url == "postgresql+psycopg://user:pw@db.example.net/costing?sslmode=require"
    monkeypatch.setenv("PC_DATABASE_URL", "sqlite:///own.db")             # an explicit setting wins
    assert Settings().database_url == "sqlite:///own.db"


def test_vercel_defaults_to_production_and_requires_secrets_and_a_database(monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.delenv("PC_ENV", raising=False)
    monkeypatch.delenv("PC_SECRET_KEY", raising=False)
    monkeypatch.delenv("PC_BOOTSTRAP_ADMIN_PASSWORD", raising=False)
    for name in ("PC_DATABASE_URL", "DATABASE_URL", "POSTGRES_URL"):
        monkeypatch.delenv(name, raising=False)
    s = Settings()
    assert (s.env, s.serverless, s.auto_migrate) == ("production", True, True)
    with pytest.raises(RuntimeError) as e:
        s.check_production()
    assert "PC_SECRET_KEY" in str(e.value) and "DATABASE_URL" in str(e.value)
    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@h/db")
    monkeypatch.setenv("PC_SECRET_KEY", STRONG)
    monkeypatch.setenv("PC_BOOTSTRAP_ADMIN_PASSWORD", "Str0ng!Passw0rd")
    Settings().check_production()                                            # no exception
    monkeypatch.setenv("PC_ENV", "development")                              # explicit choice wins
    assert Settings().env == "development"


def test_production_refuses_default_admin_password(monkeypatch):
    monkeypatch.delenv("VERCEL", raising=False)
    s = Settings(env="production", secret_key=STRONG, bootstrap_admin_password=DEV_ADMIN_PASSWORD)
    with pytest.raises(RuntimeError, match="PC_BOOTSTRAP_ADMIN_PASSWORD"):
        s.check_production()


def _fresh_app(tmp_path, monkeypatch):
    from app.db import reset_engine
    from app.main import create_app
    from app.settings import get_settings

    monkeypatch.setenv("PC_DATABASE_URL", f"sqlite+pysqlite:///{tmp_path / 'fresh.db'}")
    get_settings.cache_clear()
    reset_engine()
    return create_app(), get_settings()


def test_database_is_prepared_on_the_first_request_without_startup_events(tmp_path, monkeypatch):
    app, s = _fresh_app(tmp_path, monkeypatch)
    try:
        client = TestClient(app)          # not used as a context manager: start-up events do not run
        assert app.state.initialized is False
        assert client.get("/api/health").json()["status"] == "ok"
        assert app.state.initialized is True
        r = client.post("/api/auth/login", json={"email": s.bootstrap_admin_email,
                                                 "password": s.bootstrap_admin_password})
        assert r.status_code == 200, r.text
    finally:
        from app.db import reset_engine
        from app.settings import get_settings
        get_settings.cache_clear()
        reset_engine()


def test_sso_state_is_signed_and_cannot_be_used_as_a_login_token(tmp_path, monkeypatch):
    from app.api.auth import _check_sso_state, _sso_state

    app, _ = _fresh_app(tmp_path, monkeypatch)
    try:
        state = _sso_state()
        assert _check_sso_state(state)
        assert not _check_sso_state(state[:-2] + ("AA" if not state.endswith("AA") else "BB"))
        assert not _check_sso_state("not-a-token")
        client = TestClient(app)
        r = client.get("/api/auth/me", headers={"Authorization": f"Bearer {state}"})
        assert r.status_code == 401
    finally:
        from app.db import reset_engine
        from app.settings import get_settings
        get_settings.cache_clear()
        reset_engine()
