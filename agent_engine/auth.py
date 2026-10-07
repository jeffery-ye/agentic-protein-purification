"""
Sign-in and the current user.

The mode is fixed at startup from APP_ENV and AUTH_ENABLED (settings.py):

- dev: every request is DEV_USER. No Cognito and no session.
- shared: deploy with AUTH_ENABLED=false, for checking the image locally.
  Every request is SHARED_USER with no gate at all, so never expose it.
- cognito: deploy with AUTH_ENABLED on. The user comes from the signed session
  cookie, and a request without a valid one is a 401. There is no fallback.

Authlib runs the OIDC authorization-code flow with PKCE, including the token
exchange and the ID token's validation against the pool's JWKS. Starlette's
SessionMiddleware signs the session cookie. Nothing here does its own crypto.
"""

import re
import time
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlencode

from authlib.integrations.base_client import MismatchingStateError, OAuthError
from authlib.integrations.starlette_client import OAuth
from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from joserfc.errors import JoseError
from starlette.middleware.sessions import SessionMiddleware

from .job_store import Owner
from .settings import CognitoConfig

AuthMode = Literal["dev", "shared", "cognito"]

SESSION_COOKIE = "ppr_session"
# The cookie's lifetime, and the most a session can last from sign-in however
# active it is, which bounds how long a disabled account keeps access.
SESSION_MAX_AGE_SECONDS = 7 * 24 * 60 * 60
SCOPES = "openid email profile"
# Where sign-in returns to, held in the session between /auth/login and the callback.
NEXT_KEY = "next"
# Only the SPA's own hash routes, e.g. /#/report/<id>, so the redirect after
# sign-in can never leave the site.
RETURN_PATH = re.compile(r"/#/[A-Za-z0-9/_-]+")


@dataclass(frozen=True)
class User:
    """The signed-in user. `sub` is the identity; the username is for display."""

    sub: str
    username: str
    email: str | None

    @property
    def owner(self) -> Owner:
        return Owner(sub=self.sub, username=self.username)


# Cognito subs are UUIDs, so the local: prefix can never collide with one.
DEV_USER = User(sub="local:dev", username="dev@local", email="dev@local")
SHARED_USER = User(sub="local:shared", username="shared", email=None)


class Authenticator:
    """Resolves the current user and serves /auth/* for the startup mode."""

    def __init__(self, app_env: str, config: CognitoConfig | None):
        self.config = config
        self.mode: AuthMode
        if app_env == "dev":
            # settings.cognito_config refuses AUTH_ENABLED in dev, so config is None.
            self.mode = "dev"
        elif config is None:
            self.mode = "shared"
            print(
                "--- [Auth] WARNING: AUTH_ENABLED is off in deploy. Every request runs as the "
                "shared placeholder user with no sign-in; never expose this server ---"
            )
        else:
            self.mode = "cognito"
            self.client = _register_client(config)
            print(f"--- [Auth] Cognito sign-in required (user pool {config.user_pool_id}) ---")

    # --- Identity ------------------------------------------------------------

    def current_user(self, request: Request) -> User:
        """The request's user, or a 401 in cognito mode without a valid session."""
        if self.mode == "cognito":
            user = _session_user(request.session)
            if user is None:
                raise HTTPException(status_code=401, detail="Not signed in")
            return user
        if self.mode == "shared":
            return SHARED_USER
        return DEV_USER

    def can_change_password(self, user: User) -> bool:
        """Only real Cognito users, and never the shared reviewer account."""
        if self.mode != "cognito":
            return False
        reviewer = self.config.reviewer_username
        return reviewer is None or user.username.lower() != reviewer.lower()

    # --- Wiring --------------------------------------------------------------

    def install(self, app: FastAPI) -> None:
        """Add the session middleware (cognito mode only) and the /auth routes."""
        if self.mode == "cognito":
            app.add_middleware(
                SessionMiddleware,
                secret_key=self.config.session_secret,
                session_cookie=SESSION_COOKIE,
                max_age=SESSION_MAX_AGE_SECONDS,
                same_site="lax",
                https_only=True,
            )
        app.include_router(self._router())

    def _router(self) -> APIRouter:
        router = APIRouter(prefix="/auth", tags=["auth"])

        @router.get("/login")
        async def auth_login(request: Request, next: str | None = None):
            """Redirect to Cognito's managed sign-in page, then back to `next` (an app route)."""
            if self.mode != "cognito":
                return RedirectResponse("/", status_code=302)
            request.session.pop(NEXT_KEY, None)
            if _return_path(next):
                request.session[NEXT_KEY] = next
            return await self.client.authorize_redirect(
                request, str(request.url_for("auth_callback"))
            )

        @router.get("/callback")
        async def auth_callback(request: Request):
            """Exchange the code, validate the ID token and start the session."""
            if self.mode != "cognito":
                return RedirectResponse("/", status_code=302)
            next_path = _return_path(request.session.pop(NEXT_KEY, None)) or "/"
            try:
                token = await self.client.authorize_access_token(
                    request, claims_options=_claims_options(self.config)
                )
            except MismatchingStateError:
                # No sign-in of ours is pending, for example after Cognito's
                # password reset. The app starts a fresh one if it needs to.
                return RedirectResponse("/", status_code=302)
            except (OAuthError, JoseError) as e:
                print(f"   [Auth] Sign-in failed: {type(e).__name__}: {e}")
                raise HTTPException(status_code=401, detail="Sign-in failed")

            claims = token.get("userinfo")
            if not claims or not claims.get("sub"):
                print("   [Auth] Sign-in failed: no validated ID token in the token response")
                raise HTTPException(status_code=401, detail="Sign-in failed")

            # A fresh session, so nothing from before sign-in carries over.
            request.session.clear()
            request.session["user"] = {
                "sub": claims["sub"],
                "username": claims.get("cognito:username") or claims["sub"],
                "email": claims.get("email"),
                "signed_in_at": int(time.time()),
            }
            return RedirectResponse(next_path, status_code=302)

        @router.get("/logout")
        async def auth_logout(request: Request):
            """Clear the session, then end the Cognito session too."""
            if self.mode != "cognito":
                return RedirectResponse("/", status_code=302)
            request.session.clear()
            query = urlencode({"client_id": self.config.client_id, "logout_uri": _site(request)})
            return RedirectResponse(f"{self.config.base_url}/logout?{query}", status_code=302)

        @router.get("/me")
        async def auth_me(request: Request) -> dict[str, Any]:
            """The current user, or a 401 in cognito mode without a session."""
            user = self.current_user(request)
            can_change = self.can_change_password(user)
            return {
                "sub": user.sub,
                "username": user.username,
                "email": user.email,
                "auth_enabled": self.mode == "cognito",
                "can_change_password": can_change,
                "change_password_url": self._change_password_url(request) if can_change else None,
            }

        return router

    def _change_password_url(self, request: Request) -> str:
        """Cognito's managed reset page, which returns through /auth/callback."""
        query = urlencode(
            {
                "client_id": self.config.client_id,
                "response_type": "code",
                "scope": SCOPES,
                "redirect_uri": str(request.url_for("auth_callback")),
            }
        )
        return f"{self.config.base_url}/forgotPassword?{query}"


def _register_client(config: CognitoConfig):
    client_kwargs = {"scope": SCOPES, "code_challenge_method": "S256"}
    if config.client_secret is None:
        # A public app client: PKCE alone, no client authentication.
        client_kwargs["token_endpoint_auth_method"] = "none"
    oauth = OAuth()
    return oauth.register(
        "cognito",
        client_id=config.client_id,
        client_secret=config.client_secret,
        server_metadata_url=config.metadata_url,
        client_kwargs=client_kwargs,
    )


def _claims_options(config: CognitoConfig) -> dict[str, dict]:
    """
    Checks on the ID token beyond Authlib's defaults (signature, expiry,
    nonce): our pool issued it, for our client, and it is an ID token.
    Built per call because Authlib mutates the options it is given.
    """
    return {
        "iss": {"essential": True, "value": config.issuer},
        "aud": {"essential": True, "value": config.client_id},
        "token_use": {"essential": True, "value": "id"},
    }


def _session_user(session: dict) -> User | None:
    """The user a session holds, or None if it holds none or it has expired."""
    data = session.get("user")
    if not isinstance(data, dict):
        return None
    sub, username, email = data.get("sub"), data.get("username"), data.get("email")
    signed_in_at = data.get("signed_in_at")
    if not (isinstance(sub, str) and sub and isinstance(username, str)):
        return None
    if not isinstance(signed_in_at, int) or time.time() - signed_in_at > SESSION_MAX_AGE_SECONDS:
        return None
    return User(sub=sub, username=username, email=email if isinstance(email, str) else None)


def _return_path(value: object) -> str | None:
    """`value` if it is one of the app's own routes to return to after sign-in, else None."""
    if isinstance(value, str) and len(value) <= 200 and RETURN_PATH.fullmatch(value):
        return value
    return None


def _site(request: Request) -> str:
    """The site's origin, e.g. https://app.example.org, as Cognito's sign-out URL."""
    return str(request.base_url).rstrip("/")
