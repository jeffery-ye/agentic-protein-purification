"""
Tests for sign-in: the AUTH_ENABLED switch and Cognito
settings, the identity dependency in each mode, and the /auth routes.

main.py fixes its auth mode at import, so the app tests reload it under each
configuration. Cognito is never contacted: the OIDC metadata and a fake JWKS
are preloaded into Authlib's client, and only the token exchange (the HTTP
POST to Cognito's token endpoint) is replaced. Authlib still builds the PKCE
challenge, checks the state and validates the ID token, which the tests sign
with a throwaway RSA key.
"""

import base64
import importlib
import json
import time
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from joserfc import jwt
from joserfc.jwk import KeySet, RSAKey

from agent_engine import auth, settings
from agent_engine.job_store import Owner

POOL_ID = "us-east-1_TestPool1"
ISSUER = f"https://cognito-idp.us-east-1.amazonaws.com/{POOL_ID}"
DOMAIN = "ppr-test.auth.us-east-1.amazoncognito.com"
CLIENT_ID = "test-client-id"
SESSION_SECRET = "s" * 40

COGNITO_ENV = {
    "COGNITO_DOMAIN": DOMAIN,
    "COGNITO_USER_POOL_ID": POOL_ID,
    "COGNITO_CLIENT_ID": CLIENT_ID,
    "SESSION_SECRET_KEY": SESSION_SECRET,
}
AUTH_ENV_VARS = ["APP_ENV", "AUTH_ENABLED", "REVIEWER_USERNAME", *settings.COGNITO_ENV_VARS]

SIGNING_KEY = RSAKey.generate_key(2048, parameters={"kid": "test-key"})
OTHER_KEY = RSAKey.generate_key(2048, parameters={"kid": "test-key"})


@pytest.fixture
def env(monkeypatch):
    for name in AUTH_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def set_env(env, **values):
    for name, value in values.items():
        env.setenv(name, value)


# --- Settings ----------------------------------------------------------------


def test_sign_in_is_off_by_default_in_dev(env):
    env.setenv("APP_ENV", "dev")

    assert settings.cognito_config() is None


def test_deploy_without_auth_enabled_stops_startup(env):
    """A lost AUTH_ENABLED parameter must not open the public site."""
    env.setenv("APP_ENV", "deploy")

    with pytest.raises(ValueError, match="AUTH_ENABLED must be set in deploy"):
        settings.cognito_config()


def test_deploy_can_turn_sign_in_off_explicitly(env):
    set_env(env, APP_ENV="deploy", AUTH_ENABLED="false")

    assert settings.cognito_config() is None


def test_sign_in_on_reads_the_cognito_settings(env):
    set_env(env, APP_ENV="deploy", AUTH_ENABLED="true", REVIEWER_USERNAME="reviewer", **COGNITO_ENV)
    env.setenv("COGNITO_DOMAIN", f"https://{DOMAIN}/")

    config = settings.cognito_config()

    assert (config.domain, config.issuer, config.client_id, config.client_secret) == (
        DOMAIN,
        ISSUER,
        CLIENT_ID,
        None,
    )
    assert config.metadata_url == f"{ISSUER}/.well-known/openid-configuration"
    assert config.reviewer_username == "reviewer"


@pytest.mark.parametrize("missing", ["COGNITO_DOMAIN", "COGNITO_CLIENT_ID", "SESSION_SECRET_KEY"])
def test_sign_in_on_with_a_setting_missing_stops_startup(env, missing):
    set_env(env, APP_ENV="deploy", AUTH_ENABLED="true", **COGNITO_ENV)
    env.delenv(missing)

    with pytest.raises(ValueError, match=missing):
        settings.cognito_config()


def test_a_short_session_key_stops_startup(env):
    set_env(env, APP_ENV="deploy", AUTH_ENABLED="true", **COGNITO_ENV)
    env.setenv("SESSION_SECRET_KEY", "too-short")

    with pytest.raises(ValueError, match="SESSION_SECRET_KEY"):
        settings.cognito_config()


def test_a_malformed_user_pool_id_stops_startup(env):
    set_env(env, APP_ENV="deploy", AUTH_ENABLED="true", **COGNITO_ENV)
    env.setenv("COGNITO_USER_POOL_ID", "TestPool1")

    with pytest.raises(ValueError, match="COGNITO_USER_POOL_ID"):
        settings.cognito_config()


def test_cognito_settings_without_the_switch_stop_a_deploy(env):
    """A half-applied configuration must not quietly serve without sign-in."""
    set_env(env, APP_ENV="deploy", **COGNITO_ENV)

    with pytest.raises(ValueError, match="AUTH_ENABLED"):
        settings.cognito_config()


def test_sign_in_cannot_be_turned_on_in_dev(env):
    set_env(env, APP_ENV="dev", AUTH_ENABLED="true", **COGNITO_ENV)

    with pytest.raises(ValueError, match="APP_ENV=deploy"):
        settings.cognito_config()


def test_an_invalid_auth_enabled_is_rejected(env):
    env.setenv("AUTH_ENABLED", "maybe")

    with pytest.raises(ValueError, match="AUTH_ENABLED"):
        settings.auth_enabled()


# --- Session contents ---------------------------------------------------------


def session(**overrides):
    data = {"sub": "alice-sub", "username": "alice", "email": "a@example.org"}
    data["signed_in_at"] = int(time.time())
    data.update(overrides)
    return {"user": data}


def test_a_valid_session_is_its_user():
    assert auth._session_user(session()) == auth.User("alice-sub", "alice", "a@example.org")


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"user": "alice"},
        session(sub=""),
        session(sub=None),
        session(username=None),
        session(signed_in_at=None),
        session(signed_in_at=int(time.time()) - auth.SESSION_MAX_AGE_SECONDS - 60),
    ],
    ids=["empty", "not-a-dict", "blank-sub", "no-sub", "no-username", "no-time", "expired"],
)
def test_an_empty_malformed_or_expired_session_is_nobody(data):
    assert auth._session_user(data) is None


# --- The app in each mode -----------------------------------------------------


@pytest.fixture
def load_app(env, tmp_path, monkeypatch):
    """Reload main.py under the given environment, with a stand-in frontend."""
    (tmp_path / "index.html").write_text("<html>frontend</html>", encoding="utf-8")
    env.setenv("FRONTEND_DIST", str(tmp_path))

    import main

    def _load(**values):
        set_env(env, **values)
        module = importlib.reload(main)
        monkeypatch.setattr(module, "run_agent_task", lambda repo, job_id, request: None)
        return module

    yield _load

    for name in AUTH_ENV_VARS:
        env.delenv(name, raising=False)
    importlib.reload(main)


def https_client(module):
    return TestClient(module.app, base_url="https://testserver", follow_redirects=False)


def test_dev_signs_every_request_in_as_the_dev_user(load_app):
    client = https_client(load_app(APP_ENV="dev"))

    me = client.get("/auth/me").json()

    assert (me["username"], me["email"], me["sub"]) == ("dev@local", "dev@local", "local:dev")
    assert (me["auth_enabled"], me["can_change_password"]) == (False, False)
    assert client.get("/jobs").status_code == 200


@pytest.mark.parametrize("mode", ["dev", "deploy"])
@pytest.mark.parametrize("path", ["/auth/login", "/auth/callback", "/auth/logout"])
def test_without_sign_in_the_auth_redirects_go_home(load_app, mode, path):
    client = https_client(load_app(APP_ENV=mode, AUTH_ENABLED="false"))

    response = client.get(path)

    assert response.status_code == 302
    assert response.headers["location"] == "/"


def test_deploy_without_sign_in_runs_as_the_shared_user_not_the_dev_user(load_app, capsys):
    module = load_app(APP_ENV="deploy", AUTH_ENABLED="false")
    client = https_client(module)

    me = client.get("/auth/me").json()
    job_id = client.post("/analyze", json={"fasta_id": "P9WQA3"}).json()["job_id"]

    assert "AUTH_ENABLED is off in deploy" in capsys.readouterr().out
    assert (me["sub"], me["username"], me["auth_enabled"]) == ("local:shared", "shared", False)
    assert module.get_job_repo().get(job_id).owner == Owner(sub="local:shared", username="shared")
    assert [job["id"] for job in client.get("/jobs").json()] == [job_id]


# --- Cognito mode ------------------------------------------------------------


METADATA = {
    "issuer": ISSUER,
    "authorization_endpoint": f"https://{DOMAIN}/oauth2/authorize",
    "token_endpoint": f"https://{DOMAIN}/oauth2/token",
    "jwks_uri": f"{ISSUER}/.well-known/jwks.json",
    "id_token_signing_alg_values_supported": ["RS256"],
}


def id_token(nonce, key=SIGNING_KEY, **overrides):
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "aud": CLIENT_ID,
        "sub": "alice-sub",
        "cognito:username": "alice",
        "email": "alice@example.org",
        "token_use": "id",
        "nonce": nonce,
        "iat": now,
        "exp": now + 3600,
    }
    claims.update(overrides)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode({"alg": "RS256", "kid": "test-key"}, claims, key)


class FakeCognito:
    """Stands in for the token endpoint; everything else is Authlib's own."""

    def __init__(self, module, monkeypatch):
        self.client = module.AUTH.client
        self.client.server_metadata.update(
            METADATA,
            jwks=KeySet([SIGNING_KEY]).as_dict(private=False),
            _loaded_at=time.time(),
        )
        self.token_request = None
        self.make_token = lambda nonce: {
            "access_token": "access",
            "token_type": "Bearer",
            "id_token": id_token(nonce),
        }
        monkeypatch.setattr(self.client, "fetch_access_token", self._fetch_access_token)

    async def _fetch_access_token(self, **kwargs):
        self.token_request = kwargs
        return self.make_token(self.nonce)

    def sign_in(self, client, **login_params):
        """Run /auth/login, then Cognito's redirect back to /auth/callback."""
        login = client.get("/auth/login", params=login_params)
        query = parse_qs(urlparse(login.headers["location"]).query)
        self.authorize_query = query
        self.nonce = query["nonce"][0]
        return client.get("/auth/callback", params={"code": "the-code", "state": query["state"][0]})


@pytest.fixture
def cognito(load_app, monkeypatch):
    def _start(**extra_env):
        module = load_app(APP_ENV="deploy", AUTH_ENABLED="true", **{**COGNITO_ENV, **extra_env})
        return module, https_client(module), FakeCognito(module, monkeypatch)

    return _start


@pytest.mark.parametrize(
    "method, path",
    [
        ("get", "/auth/me"),
        ("get", "/jobs"),
        ("get", "/status/some-job"),
        ("get", "/result/some-job"),
        ("post", "/analyze"),
        ("delete", "/jobs/some-job"),
        ("post", "/jobs/delete"),
    ],
)
def test_deploy_rejects_requests_without_a_session(cognito, method, path):
    _, client, _ = cognito()

    response = getattr(client, method)(path)

    assert response.status_code == 401


def test_deploy_never_falls_back_to_the_dev_or_shared_user(cognito):
    module, client, _ = cognito()
    for user in (auth.DEV_USER, auth.SHARED_USER):
        module.get_job_repo().create(
            f"{user.sub}-job", {"fasta_id": "P9WQA3"}, user.owner, None, None
        )

    responses = [
        client.get("/auth/me"),
        client.get("/jobs"),
        client.get("/status/local:dev-job"),
        client.get("/status/local:shared-job"),
    ]

    assert [r.status_code for r in responses] == [401, 401, 401, 401]
    assert all("dev@local" not in r.text and "shared" not in r.text for r in responses)


def test_health_and_the_frontend_stay_public(cognito):
    _, client, _ = cognito()

    assert client.get("/health").status_code == 200
    assert "frontend" in client.get("/").text


def test_login_redirects_to_cognito_with_pkce(cognito):
    _, client, fake = cognito()

    response = client.get("/auth/login")

    location = urlparse(response.headers["location"])
    query = parse_qs(location.query)
    assert response.status_code == 302
    assert (
        f"{location.scheme}://{location.netloc}{location.path}"
        == METADATA["authorization_endpoint"]
    )
    assert query["client_id"] == [CLIENT_ID]
    assert query["redirect_uri"] == ["https://testserver/auth/callback"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["code_challenge"] and query["state"] and query["nonce"]
    assert query["scope"] == ["openid email profile"]


def test_signing_in_starts_a_session_for_the_cognito_user(cognito):
    module, client, fake = cognito()

    callback = fake.sign_in(client)
    me = client.get("/auth/me")

    assert (callback.status_code, callback.headers["location"]) == (302, "/")
    assert me.status_code == 200
    assert {k: me.json()[k] for k in ["sub", "username", "email", "auth_enabled"]} == {
        "sub": "alice-sub",
        "username": "alice",
        "email": "alice@example.org",
        "auth_enabled": True,
    }
    # PKCE: the verifier and the exact redirect URI went to the token endpoint.
    assert fake.token_request["code"] == "the-code"
    assert fake.token_request["code_verifier"]
    assert fake.token_request["redirect_uri"] == "https://testserver/auth/callback"


def test_signing_in_returns_to_the_page_that_asked(cognito):
    _, client, fake = cognito()

    callback = fake.sign_in(client, next="/#/report/0b6e-job_1")

    assert (callback.status_code, callback.headers["location"]) == (302, "/#/report/0b6e-job_1")


@pytest.mark.parametrize(
    "next_path",
    [
        "https://evil.example/#/report/x",
        "//evil.example/#/report/x",
        "/\\evil.example",
        "/#/",
        "/#/report/x?y=1",
        "/#/report/x@evil.example",
        "/jobs",
        "",
        "/#/" + "a" * 300,
    ],
)
def test_signing_in_never_returns_outside_the_apps_routes(cognito, next_path):
    _, client, fake = cognito()

    callback = fake.sign_in(client, next=next_path)

    assert (callback.status_code, callback.headers["location"]) == (302, "/")


def test_the_session_cookie_is_http_only_secure_lax_and_holds_no_tokens(cognito):
    _, client, fake = cognito()

    callback = fake.sign_in(client)

    cookie = callback.headers["set-cookie"]
    assert cookie.startswith(f"{auth.SESSION_COOKIE}=")
    assert "httponly" in cookie.lower()
    assert "secure" in cookie.lower()
    assert "samesite=lax" in cookie.lower()
    assert f"max-age={auth.SESSION_MAX_AGE_SECONDS}" in cookie.lower()
    payload = cookie.split("=", 1)[1].split(";")[0].split(".")[0]
    data = json.loads(base64.b64decode(payload + "=" * (-len(payload) % 4)))
    assert data == {
        "user": {
            "sub": "alice-sub",
            "username": "alice",
            "email": "alice@example.org",
            "signed_in_at": data["user"]["signed_in_at"],
        }
    }


def test_a_signed_in_users_jobs_are_theirs(cognito):
    module, client, fake = cognito()
    fake.sign_in(client)

    job_id = client.post("/analyze", json={"fasta_id": "P9WQA3"}).json()["job_id"]

    assert module.get_job_repo().get(job_id).owner == Owner(sub="alice-sub", username="alice")
    assert client.get(f"/status/{job_id}").status_code == 200
    assert [job["id"] for job in client.get("/jobs").json()] == [job_id]


def test_logout_clears_the_session_and_ends_the_cognito_session(cognito):
    _, client, fake = cognito()
    fake.sign_in(client)

    response = client.get("/auth/logout")

    location = urlparse(response.headers["location"])
    assert response.status_code == 302
    assert f"{location.scheme}://{location.netloc}{location.path}" == f"https://{DOMAIN}/logout"
    assert parse_qs(location.query) == {
        "client_id": [CLIENT_ID],
        "logout_uri": ["https://testserver"],
    }
    assert client.get("/auth/me").status_code == 401


def test_a_forged_session_cookie_is_rejected(cognito):
    _, client, _ = cognito()
    forged = base64.b64encode(json.dumps(session()).encode()).decode()
    client.cookies.set(auth.SESSION_COOKIE, f"{forged}.AAAAAA.forged-signature")

    assert client.get("/auth/me").status_code == 401


def test_a_session_signed_with_another_key_is_rejected(cognito, load_app, monkeypatch):
    _, other_client, other_fake = cognito(SESSION_SECRET_KEY="o" * 40)
    other_fake.sign_in(other_client)
    stolen = other_client.cookies[auth.SESSION_COOKIE]

    _, client, _ = cognito()
    client.cookies.set(auth.SESSION_COOKIE, stolen)

    assert client.get("/auth/me").status_code == 401


@pytest.mark.parametrize(
    "token",
    [
        lambda nonce: {"access_token": "a", "id_token": id_token(nonce, key=OTHER_KEY)},
        lambda nonce: {"access_token": "a", "id_token": id_token(nonce, aud="another-client")},
        lambda nonce: {
            "access_token": "a",
            "id_token": id_token(nonce, iss="https://evil.example"),
        },
        lambda nonce: {"access_token": "a", "id_token": id_token("another-nonce")},
        lambda nonce: {
            "access_token": "a",
            "id_token": id_token(nonce, exp=int(time.time()) - 600),
        },
        lambda nonce: {"access_token": "a", "id_token": id_token(nonce, token_use="access")},
        lambda nonce: {"access_token": "a", "id_token": id_token(nonce, token_use=None)},
        lambda nonce: {"access_token": "a"},
    ],
    ids=[
        "wrong-key",
        "wrong-audience",
        "wrong-issuer",
        "wrong-nonce",
        "expired",
        "access-token",
        "no-token-use",
        "no-id-token",
    ],
)
def test_an_invalid_id_token_does_not_sign_in(cognito, token):
    _, client, fake = cognito()
    fake.make_token = token

    callback = fake.sign_in(client)

    assert callback.status_code == 401
    assert client.get("/auth/me").status_code == 401


def test_a_callback_without_a_pending_sign_in_goes_home_signed_out(cognito):
    _, client, fake = cognito()

    response = client.get("/auth/callback", params={"code": "c", "state": "unknown"})

    assert (response.status_code, response.headers["location"]) == (302, "/")
    assert fake.token_request is None
    assert client.get("/auth/me").status_code == 401


def test_a_cognito_error_on_the_callback_does_not_sign_in(cognito):
    _, client, fake = cognito()
    client.get("/auth/login")

    response = client.get("/auth/callback", params={"error": "access_denied"})

    assert response.status_code == 401
    assert fake.token_request is None


def test_users_can_change_their_password_through_cognito(cognito):
    _, client, fake = cognito(REVIEWER_USERNAME="reviewer")
    fake.sign_in(client)

    me = client.get("/auth/me").json()

    url = urlparse(me["change_password_url"])
    assert me["can_change_password"] is True
    assert f"{url.scheme}://{url.netloc}{url.path}" == f"https://{DOMAIN}/forgotPassword"
    assert parse_qs(url.query)["redirect_uri"] == ["https://testserver/auth/callback"]


def test_the_reviewer_account_cannot_change_its_password(cognito):
    _, client, fake = cognito(REVIEWER_USERNAME="Reviewer")
    fake.make_token = lambda nonce: {
        "access_token": "a",
        "id_token": id_token(nonce, **{"cognito:username": "reviewer", "sub": "reviewer-sub"}),
    }
    fake.sign_in(client)

    me = client.get("/auth/me").json()

    assert (me["username"], me["can_change_password"], me["change_password_url"]) == (
        "reviewer",
        False,
        None,
    )
