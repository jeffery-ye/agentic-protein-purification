"""
Tests for the APP_ENV runtime mode: settings parsing, the mode-dependent app
wiring in main.py, and the internal-data switch.

main.py reads APP_ENV at import, so the app tests reload it under each mode.
"""

import importlib

import pytest
from fastapi.testclient import TestClient

from agent_engine import settings
from agent_engine.agents import agent_body
from agent_engine.agents.agent_body import AgentResult, ProteinPurificationAgent


@pytest.fixture
def env(monkeypatch):
    for name in ["APP_ENV", "INTERNAL_DATA", "DEV_CORS_ORIGINS"]:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


# --- Settings ----------------------------------------------------------------


def test_app_env_defaults_to_dev(env):
    assert settings.app_env() == "dev"


def test_unknown_app_env_is_rejected(env):
    env.setenv("APP_ENV", "production")

    with pytest.raises(ValueError, match="APP_ENV"):
        settings.app_env()


@pytest.mark.parametrize("mode, expected", [("dev", True), ("deploy", False)])
def test_internal_data_follows_the_mode_by_default(env, mode, expected):
    env.setenv("APP_ENV", mode)

    assert settings.internal_data_enabled() is expected


@pytest.mark.parametrize("mode, value, expected", [("deploy", "true", True), ("dev", "0", False)])
def test_internal_data_overrides_the_mode(env, mode, value, expected):
    env.setenv("APP_ENV", mode)
    env.setenv("INTERNAL_DATA", value)

    assert settings.internal_data_enabled() is expected


def test_invalid_internal_data_is_rejected(env):
    env.setenv("INTERNAL_DATA", "maybe")

    with pytest.raises(ValueError, match="INTERNAL_DATA"):
        settings.internal_data_enabled()


def test_the_job_database_defaults_to_the_repository_data_directory_in_dev(env):
    env.delenv("DATA_DIR")

    assert settings.job_db_path() == settings.DEFAULT_DATA_DIR / "jobs.sqlite3"


def test_data_dir_sets_the_job_database_location(env, tmp_path):
    env.setenv("DATA_DIR", str(tmp_path))

    assert settings.job_db_path() == tmp_path / "jobs.sqlite3"


def test_deploy_requires_data_dir(env):
    env.setenv("APP_ENV", "deploy")
    env.delenv("DATA_DIR")

    with pytest.raises(ValueError, match="DATA_DIR"):
        settings.data_dir()


@pytest.mark.parametrize("value, expected", [("abc123", "abc123"), ("", None), (None, None)])
def test_app_version_comes_from_the_environment(env, value, expected):
    if value is None:
        env.delenv("APP_VERSION", raising=False)
    else:
        env.setenv("APP_VERSION", value)

    assert settings.app_version() == expected


# --- App wiring --------------------------------------------------------------


@pytest.fixture
def load_app(env, tmp_path):
    """Reload main.py under a given APP_ENV, with a stand-in frontend build."""
    (tmp_path / "index.html").write_text("<html>frontend</html>", encoding="utf-8")
    env.setenv("FRONTEND_DIST", str(tmp_path))

    import main

    def _load(mode):
        env.setenv("APP_ENV", mode)
        return TestClient(importlib.reload(main).app)

    yield _load

    env.delenv("APP_ENV")
    importlib.reload(main)


CORS_PREFLIGHT = {
    "Origin": "http://localhost:5173",
    "Access-Control-Request-Method": "POST",
}


@pytest.mark.parametrize("origin", ["http://localhost:5173", "http://127.0.0.1:5173"])
def test_dev_allows_the_vite_origins(load_app, origin):
    client = load_app("dev")

    response = client.options("/analyze", headers={**CORS_PREFLIGHT, "Origin": origin})

    assert response.headers.get("access-control-allow-origin") == origin


def test_dev_refuses_other_origins(load_app):
    client = load_app("dev")

    response = client.options(
        "/analyze", headers={**CORS_PREFLIGHT, "Origin": "https://evil.example"}
    )

    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


def test_dev_cors_origins_are_configurable(env):
    env.setenv("DEV_CORS_ORIGINS", " http://localhost:3000/ , http://localhost:4173")

    assert settings.dev_cors_origins() == ["http://localhost:3000", "http://localhost:4173"]


def test_dev_cors_origins_refuse_a_wildcard(env):
    env.setenv("DEV_CORS_ORIGINS", "*")

    with pytest.raises(ValueError, match="not '\\*'"):
        settings.dev_cors_origins()


def test_deploy_installs_no_cors(load_app):
    client = load_app("deploy")

    response = client.options("/analyze", headers=CORS_PREFLIGHT)

    assert "access-control-allow-origin" not in response.headers


@pytest.mark.parametrize("mode", ["dev", "deploy"])
def test_health_is_static(load_app, mode):
    response = load_app(mode).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_deploy_serves_the_frontend_without_shadowing_the_api(load_app):
    client = load_app("deploy")

    assert "frontend" in client.get("/").text
    assert client.get("/openapi.json").status_code == 200
    assert client.get("/docs").status_code == 200
    assert client.get("/status/unknown").status_code == 404
    assert client.get("/status/unknown").json() == {"detail": "Job not found"}


def test_dev_does_not_serve_the_frontend(load_app):
    assert load_app("dev").get("/").status_code == 404


def test_unknown_mode_stops_the_app_at_import(load_app):
    with pytest.raises(ValueError, match="APP_ENV"):
        load_app("staging")


# --- Internal-data switch ----------------------------------------------------


@pytest.fixture
def resolve(monkeypatch):
    class _Stub:
        def find_protocol(self, text):
            return []

    monkeypatch.setattr(agent_body, "ProtocolAgent", _Stub)

    def _resolve(value):
        return ProteinPurificationAgent()._resolve_input(value, lambda _msg: None)

    return _resolve


def test_ssgcid_id_without_internal_data_fails_with_a_clear_message(env, resolve):
    env.setenv("APP_ENV", "deploy")
    env.setattr(agent_body, "get_cttdb_info", lambda _id: pytest.fail("CTTdb was queried"))

    result = resolve("MytuD.00516.a")

    assert isinstance(result, AgentResult)
    assert result.success is False
    assert "looks like an SSGCID ID" in result.error_message
    assert "FASTA" in result.error_message


def test_non_ssgcid_input_is_unaffected_without_internal_data(env, resolve):
    env.setenv("APP_ENV", "deploy")

    assert resolve("P9WQA3") == ("P9WQA3", "", None)
