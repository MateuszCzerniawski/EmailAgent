"""
Auth router — Gmail OAuth2 flow.

Endpoints
---------
GET /auth/status
    Returns whether Gmail credentials are already stored in keyring.

GET /auth/gmail
    Initiates the OAuth2 consent flow.
    - If credentials exist → 200 {"status": "already_authenticated"}.
    - If credentials missing → 302 redirect to Google consent page.

GET /auth/callback
    OAuth2 redirect target. Exchanges the authorisation code for a refresh
    token and stores it in keyring.

Implementation notes
--------------------
- Uses google_auth_oauthlib.flow.Flow (web-based variant), NOT
  InstalledAppFlow, because the redirect_uri must be a real HTTP endpoint.
- OAuth state tokens are stored in app.state.oauth_states (in-memory dict).
  This is sufficient for a single-process dev server; a Redis-backed store
  would be needed for multi-process deployments.
- After the callback the user is redirected to GET /emails so they can
  immediately see results in the browser.
"""

from __future__ import annotations

import secrets

import keyring
from fastapi import APIRouter, Depends, HTTPException, Request
from app.logger import get_logger
from fastapi.responses import JSONResponse, RedirectResponse
from google_auth_oauthlib.flow import Flow

from app.config import AppConfig
from app.dependencies import get_config

logger = get_logger(__name__)
router = APIRouter()

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_flow(config: AppConfig, redirect_uri: str) -> Flow:
    """
    Build a google_auth_oauthlib Flow from credentials stored in keyring.

    Raises HTTPException 503 if client_id / client_secret are missing.
    """
    kr = config.keyring
    client_id = keyring.get_password(kr.service_name, kr.gmail_client_id_key_name)
    client_secret = keyring.get_password(kr.service_name, kr.gmail_client_secret_key_name)

    if not client_id or not client_secret:
        raise HTTPException(
            status_code=503,
            detail=(
                "Gmail OAuth credentials not configured. "
                "Run `python -m uv run setup-secrets` first."
            ),
        )

    client_config = {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [redirect_uri],
        }
    }
    return Flow.from_client_config(client_config, scopes=SCOPES, redirect_uri=redirect_uri)


def _callback_uri(request: Request) -> str:
    """Build the absolute callback URL from the current request's base URL."""
    base = str(request.base_url).rstrip("/")
    return f"{base}/auth/callback"


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/status", summary="Check Gmail authentication status")
def auth_status(config: AppConfig = Depends(get_config)):
    """
    Return whether a Gmail refresh token is already stored in keyring.

    Response:
        {"authenticated": true/false}
    """
    kr = config.keyring
    token = keyring.get_password(kr.service_name, kr.gmail_token_key_name)
    return {"authenticated": bool(token)}


@router.get("/gmail", summary="Start Gmail OAuth2 flow")
def start_gmail_auth(request: Request, config: AppConfig = Depends(get_config)):
    """
    Check if already authenticated; if not, redirect to Google consent page.

    If a refresh token is already stored in keyring the endpoint returns 200
    instead of triggering a new consent flow.
    """
    kr = config.keyring
    existing_token = keyring.get_password(kr.service_name, kr.gmail_token_key_name)
    if existing_token:
        return JSONResponse(
            {"status": "already_authenticated", "message": "Gmail is connected."}
        )

    # Initialise the OAuth flow and generate the consent URL.
    redirect_uri = _callback_uri(request)
    flow = _build_flow(config, redirect_uri)
    auth_url, state = flow.authorization_url(
        access_type="offline",   # request a refresh token
        prompt="consent",        # force consent screen to always return refresh_token
        include_granted_scopes="true",
    )

    # Persist the flow object keyed by state so the callback can retrieve it.
    if not hasattr(request.app.state, "oauth_states"):
        request.app.state.oauth_states = {}
    request.app.state.oauth_states[state] = flow

    logger.info("Redirecting to Google consent URL (state=%s)", state[:8])
    return RedirectResponse(url=auth_url)


@router.get("/callback", summary="Gmail OAuth2 callback")
def gmail_callback(
    request: Request,
    code: str,
    state: str,
    config: AppConfig = Depends(get_config),
):
    """
    Exchange the authorisation code for tokens and store the refresh token.

    Google redirects here after the user grants (or denies) consent.
    Query params injected by Google: code, state, scope.
    """
    # Retrieve the previously stored flow object.
    oauth_states: dict = getattr(request.app.state, "oauth_states", {})
    flow = oauth_states.pop(state, None)

    if flow is None:
        raise HTTPException(
            status_code=400,
            detail="Invalid or expired OAuth state. Please start the flow again at /auth/gmail.",
        )

    try:
        redirect_uri = _callback_uri(request)
        flow.redirect_uri = redirect_uri
        flow.fetch_token(code=code)
    except Exception as exc:
        logger.error("OAuth token exchange failed: %s", exc)
        raise HTTPException(
            status_code=400,
            detail=f"Failed to exchange authorisation code: {exc}",
        ) from exc

    creds = flow.credentials
    if not creds.refresh_token:
        raise HTTPException(
            status_code=400,
            detail=(
                "No refresh token received. Revoke access at "
                "https://myaccount.google.com/permissions and try again."
            ),
        )

    # Persist the refresh token.
    kr = config.keyring
    keyring.set_password(kr.service_name, kr.gmail_token_key_name, creds.refresh_token)
    logger.info("Gmail refresh token stored in keyring successfully.")

    # Redirect to the emails list so the user can see results immediately.
    return RedirectResponse(url="/emails")


@router.delete("/gmail", summary="Revoke stored Gmail credentials")
def revoke_gmail_auth(config: AppConfig = Depends(get_config)):
    """
    Remove the Gmail refresh token from keyring.

    The token is NOT revoked on Google's side — use
    https://myaccount.google.com/permissions for that.
    """
    kr = config.keyring
    try:
        keyring.delete_password(kr.service_name, kr.gmail_token_key_name)
        return {"status": "revoked", "message": "Gmail token removed from keyring."}
    except keyring.errors.PasswordDeleteError:
        return {"status": "not_found", "message": "No Gmail token was stored."}
