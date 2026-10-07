"""
Runtime mode for the application.

APP_ENV selects how one codebase runs: "dev" (the default) is the local
development server, "deploy" is the container behind Caddy. It changes runtime
behaviour only and never touches infrastructure.

Values are read at call time rather than cached, so tests can switch modes with
monkeypatch and the container picks up whatever its env file supplies.
"""

import os
import re
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

APP_ENVS = ("dev", "deploy")

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def app_env() -> str:
    """Return the runtime mode, rejecting anything other than dev or deploy."""
    value = (os.getenv("APP_ENV") or "dev").strip().lower()
    if value not in APP_ENVS:
        raise ValueError(f"APP_ENV must be one of {list(APP_ENVS)}; got {value!r}")
    return value


def is_deploy() -> bool:
    return app_env() == "deploy"


def internal_data_enabled() -> bool:
    """
    Whether internal SSGCID data (CTTdb) may be used.

    INTERNAL_DATA overrides the mode default: on in dev, off in deploy, where
    the database is unreachable because it relies on Windows integrated auth.
    """
    raw = os.getenv("INTERNAL_DATA")
    if raw is None or not raw.strip():
        return not is_deploy()

    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise ValueError(f"INTERNAL_DATA must be true or false; got {raw!r}")


# The dev default for DATA_DIR: a gitignored directory at the repository root.
DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
JOB_DB_FILENAME = "jobs.sqlite3"


def data_dir() -> Path:
    """
    Where persistent application state (the job database) lives.

    DATA_DIR sets it. dev falls back to the repository's data/ directory;
    deploy requires it, so the container never writes jobs to its own
    throwaway filesystem.
    """
    raw = os.getenv("DATA_DIR")
    if raw and raw.strip():
        return Path(raw.strip())
    if is_deploy():
        raise ValueError("DATA_DIR must be set in deploy (the mounted data volume)")
    return DEFAULT_DATA_DIR


def job_db_path() -> Path:
    return data_dir() / JOB_DB_FILENAME


def entrez_email() -> str:
    """
    The contact address NCBI's usage policy requires on every Entrez request.
    Checked when the server starts and when a pipeline run starts, not at import,
    so the agent modules import without it.
    """
    value = (os.getenv("ENTREZ_EMAIL") or "").strip()
    if not value:
        raise ValueError("ENTREZ_EMAIL environment variable not set")
    return value


def ncbi_api_key() -> str | None:
    """NCBI_API_KEY, sent with every PMC fetch when set; it raises NCBI's rate limit."""
    value = (os.getenv("NCBI_API_KEY") or "").strip()
    return value or None


def app_version() -> str | None:
    """The commit SHA of the running build, from APP_VERSION, or None when unset."""
    value = (os.getenv("APP_VERSION") or "").strip()
    return value or None


# --- Job caps (#26) ----------------------------------------------------------

# Each job holds a threadpool slot and LLM calls for minutes.
DEFAULT_MAX_ACTIVE_JOBS = 2


def _positive_int(name: str) -> int | None:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"{name} must be a whole number; got {raw!r}") from None
    if value < 1:
        raise ValueError(f"{name} must be at least 1; got {value}")
    return value


def daily_job_cap() -> int | None:
    """
    DAILY_JOB_CAP: the most jobs the whole service starts per UTC day. Required
    in deploy, where it bounds LLM spend, so a missing value stops startup
    rather than leaving spend uncapped. dev has no cap unless it is set.
    """
    value = _positive_int("DAILY_JOB_CAP")
    if value is None and is_deploy():
        raise ValueError("DAILY_JOB_CAP must be set in deploy")
    return value


def max_active_jobs() -> int:
    """
    MAX_ACTIVE_JOBS: the most queued or running jobs at once, across all users.
    Defaults to 2 in both modes, so a stray dev request can't fan out LLM spend.
    """
    return _positive_int("MAX_ACTIVE_JOBS") or DEFAULT_MAX_ACTIVE_JOBS


# --- Dev CORS ----------------------------------------------------------------

# The Vite dev server. Browsers treat localhost and 127.0.0.1 as different origins.
DEFAULT_DEV_CORS_ORIGINS = ("http://localhost:5173", "http://127.0.0.1:5173")


def dev_cors_origins() -> list[str]:
    """
    DEV_CORS_ORIGINS: comma-separated origins the dev server accepts
    cross-origin requests from. Defaults to the Vite dev server. deploy serves
    the frontend on its own origin and installs no CORS at all.
    """
    raw = os.getenv("DEV_CORS_ORIGINS") or ""
    origins = [o.strip().rstrip("/") for o in raw.split(",") if o.strip()]
    if "*" in origins:
        raise ValueError("DEV_CORS_ORIGINS must list origins, not '*'")
    return origins or list(DEFAULT_DEV_CORS_ORIGINS)


# A full run takes minutes; an hour is well past the slowest one seen, so only a
# job that is genuinely stuck reaches it (#95).
DEFAULT_JOB_DEADLINE_MINUTES = 60


def job_deadline_minutes() -> int:
    """
    JOB_DEADLINE_MINUTES: how long a job may stay queued or running before the
    reaper fails it and frees its active-cap slot. Defaults to 60 in both modes.
    """
    return _positive_int("JOB_DEADLINE_MINUTES") or DEFAULT_JOB_DEADLINE_MINUTES


# --- Sign-in -----------------------------------------------------------------

# The Cognito and session settings. In deploy, Parameter Store supplies them
# through ppr-start.sh like every other /ppr/ parameter.
COGNITO_ENV_VARS = (
    "COGNITO_DOMAIN",
    "COGNITO_USER_POOL_ID",
    "COGNITO_CLIENT_ID",
    "COGNITO_CLIENT_SECRET",
    "SESSION_SECRET_KEY",
)

# itsdangerous signs with HMAC-SHA1 via a derived key; 32 characters of random
# text is the floor for the signing key.
MIN_SESSION_SECRET_LENGTH = 32

_USER_POOL_ID = re.compile(r"^([a-z]{2}(?:-[a-z]+)+-\d)_[A-Za-z0-9]+$")


@dataclass(frozen=True)
class CognitoConfig:
    """Everything the app needs to run Cognito sign-in and keep the session."""

    domain: str
    user_pool_id: str
    client_id: str
    client_secret: str | None
    session_secret: str
    reviewer_username: str | None

    @property
    def region(self) -> str:
        return _USER_POOL_ID.match(self.user_pool_id).group(1)

    @property
    def issuer(self) -> str:
        return f"https://cognito-idp.{self.region}.amazonaws.com/{self.user_pool_id}"

    @property
    def metadata_url(self) -> str:
        return f"{self.issuer}/.well-known/openid-configuration"

    @property
    def base_url(self) -> str:
        """The managed login pages, e.g. https://<prefix>.auth.us-east-1.amazoncognito.com."""
        return f"https://{self.domain}"


def _env(name: str) -> str | None:
    value = (os.getenv(name) or "").strip()
    return value or None


def auth_enabled() -> bool:
    """AUTH_ENABLED, off when unset. See cognito_config for what it switches."""
    raw = _env("AUTH_ENABLED")
    if raw is None:
        return False
    value = raw.lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise ValueError(f"AUTH_ENABLED must be true or false; got {raw!r}")


def cognito_config() -> CognitoConfig | None:
    """
    The Cognito configuration when sign-in is on, or None when it is off.

    AUTH_ENABLED is the switch, and it is explicit rather than inferred from
    which settings happen to be present:

    - Unset: off in dev. In deploy, startup stops, so a lost parameter can
      never open the public site without sign-in.
    - false: None. In deploy, every request runs as one shared placeholder
      user with no gate at all, which is only for checking the image locally.
    - true: every setting below is required, or startup stops. Only deploy can
      turn it on, so a server started in dev by mistake never serves real
      users as the dev user.
    - Off in deploy while any Cognito setting is present is a half-applied
      configuration, and startup stops rather than serving without sign-in.
    """
    if is_deploy() and _env("AUTH_ENABLED") is None:
        raise ValueError(
            "AUTH_ENABLED must be set in deploy: true to require Cognito sign-in, or false "
            "to run without sign-in (local image checks only)"
        )
    if not auth_enabled():
        present = [name for name in COGNITO_ENV_VARS if _env(name)]
        if is_deploy() and present:
            raise ValueError(
                f"{', '.join(present)} set but AUTH_ENABLED is not true. Set AUTH_ENABLED=true "
                "to require sign-in, or remove the Cognito settings."
            )
        return None

    if not is_deploy():
        raise ValueError("AUTH_ENABLED=true requires APP_ENV=deploy; dev always signs in as dev")

    required = ["COGNITO_DOMAIN", "COGNITO_USER_POOL_ID", "COGNITO_CLIENT_ID", "SESSION_SECRET_KEY"]
    missing = [name for name in required if not _env(name)]
    if missing:
        raise ValueError(f"AUTH_ENABLED=true but {', '.join(missing)} not set")

    pool_id = _env("COGNITO_USER_POOL_ID")
    if not _USER_POOL_ID.match(pool_id):
        raise ValueError(f"COGNITO_USER_POOL_ID must look like us-east-1_AbC123; got {pool_id!r}")

    secret = _env("SESSION_SECRET_KEY")
    if len(secret) < MIN_SESSION_SECRET_LENGTH:
        raise ValueError(
            f"SESSION_SECRET_KEY must be at least {MIN_SESSION_SECRET_LENGTH} characters"
        )

    domain = _env("COGNITO_DOMAIN").removeprefix("https://").rstrip("/")
    return CognitoConfig(
        domain=domain,
        user_pool_id=pool_id,
        client_id=_env("COGNITO_CLIENT_ID"),
        client_secret=_env("COGNITO_CLIENT_SECRET"),
        session_secret=secret,
        reviewer_username=_env("REVIEWER_USERNAME"),
    )
