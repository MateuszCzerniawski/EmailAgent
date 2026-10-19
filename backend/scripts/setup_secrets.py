"""
First-time secret setup script.

Stores all required credentials in the OS keyring so the server can start
without any .env file or hard-coded secrets.

Usage:
    python -m uv run setup-secrets
    # or directly:
    python scripts/setup_secrets.py

What it stores
--------------
1. OpenAI API key          → keyring[service_name]["openai_api_key"]
2. Google OAuth client_id  → keyring[service_name]["gmail_client_id"]
3. Google OAuth client_secret → keyring[service_name]["gmail_client_secret"]
4. Gmail refresh token     → keyring[service_name]["gmail_refresh_token"]
   (obtained via browser-based OAuth consent — opens a local browser tab)

Where secrets are stored
------------------------
- Windows : Windows Credential Manager (Control Panel → Credential Manager)
- macOS   : Keychain Access
- Linux   : GNOME Secret Service / KWallet

Re-running this script safely overwrites previously stored values.
"""

from __future__ import annotations

import getpass
import sys
from pathlib import Path

import keyring
import yaml
from google_auth_oauthlib.flow import InstalledAppFlow

# Gmail OAuth scopes — readonly for fetching, send for outbound emails.
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]

# Config file is one directory up from this script (backend/config.yaml).
_CONFIG_PATH = Path(__file__).parent.parent / "config" / "config.yaml"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_keyring_config() -> dict:
    """Read the keyring namespace settings from config.yaml."""
    if not _CONFIG_PATH.exists():
        sys.exit(
            f"ERROR: config.yaml not found at {_CONFIG_PATH}\n"
            "Please ensure you are running this script from the backend/ directory."
        )
    with _CONFIG_PATH.open(encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    return cfg.get("keyring", {})


def _prompt_secret(prompt: str) -> str | None:
    """Prompt for a secret with hidden input. Returns None if user skips."""
    value = getpass.getpass(f"{prompt} (press Enter to skip): ").strip()
    return value if value else None


# ---------------------------------------------------------------------------
# Setup steps
# ---------------------------------------------------------------------------


def setup_openai_key(kr: dict) -> None:
    """Store the OpenAI API key in keyring."""
    print("\n--- Step 1: OpenAI API Key ---")
    print("Find your key at: https://platform.openai.com/api-keys")
    key = _prompt_secret("Paste your OpenAI API key")
    if not key:
        print("Skipped.")
        return
    keyring.set_password(kr["service_name"], kr["openai_key_name"], key)
    print("OpenAI API key stored in keyring.")


def setup_gmail_credentials(kr: dict) -> None:
    """
    Store Gmail OAuth client_id and client_secret in keyring, then run the
    OAuth consent flow to obtain and store the refresh token.

    The user must:
    1. Create a Google Cloud project with the Gmail API enabled.
    2. Create an OAuth 2.0 Desktop App credential.
    3. Provide the client_id and client_secret when prompted.

    Alternatively, if they have a credentials.json file downloaded from the
    Google Cloud Console, the script can read from it automatically.
    """
    print("\n--- Step 2: Gmail OAuth2 Credentials ---")
    print(
        "You need a Google Cloud project with Gmail API enabled.\n"
        "Create OAuth 2.0 credentials at:\n"
        "  https://console.cloud.google.com/apis/credentials\n"
        "Choose 'Desktop app' as the application type.\n"
    )

    # Option A: read from a downloaded credentials.json file.
    credentials_json = Path(__file__).parent.parent / "credentials.json"
    if credentials_json.exists():
        print(f"Found credentials.json at {credentials_json} — using it.")
        _run_oauth_flow_from_file(credentials_json, kr)
        return

    # Option B: manual entry of client_id and client_secret.
    print("No credentials.json found. Enter your OAuth credentials manually.")
    client_id = _prompt_secret("Google OAuth2 Client ID")
    client_secret = _prompt_secret("Google OAuth2 Client Secret")

    if not client_id or not client_secret:
        print("Skipped Gmail setup (client_id or client_secret missing).")
        return

    keyring.set_password(kr["service_name"], kr["gmail_client_id_key_name"], client_id)
    keyring.set_password(kr["service_name"], kr["gmail_client_secret_key_name"], client_secret)
    print("Client credentials stored in keyring.")

    # Run the consent flow using the manually entered credentials.
    _run_oauth_flow_from_credentials(client_id, client_secret, kr)


def _run_oauth_flow_from_file(credentials_json: Path, kr: dict) -> None:
    """Run InstalledAppFlow from a downloaded credentials.json file."""
    import json

    with credentials_json.open() as f:
        cred_data = json.load(f)

    # credentials.json has either "web" or "installed" top-level key.
    info = cred_data.get("installed") or cred_data.get("web", {})
    client_id = info.get("client_id", "")
    client_secret = info.get("client_secret", "")

    # Store client_id and client_secret in keyring for the server to use.
    keyring.set_password(kr["service_name"], kr["gmail_client_id_key_name"], client_id)
    keyring.set_password(kr["service_name"], kr["gmail_client_secret_key_name"], client_secret)

    flow = InstalledAppFlow.from_client_secrets_file(str(credentials_json), SCOPES)
    _complete_flow(flow, kr)


def _run_oauth_flow_from_credentials(client_id: str, client_secret: str, kr: dict) -> None:
    """Run InstalledAppFlow with manually entered credentials."""
    client_config = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["urn:ietf:wg:oauth:2.0:oob", "http://localhost"],
        }
    }
    flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
    _complete_flow(flow, kr)


def _complete_flow(flow: InstalledAppFlow, kr: dict) -> None:
    """
    Open a local browser, complete the OAuth consent screen, then store the
    refresh token in keyring.

    port=0 lets the OS pick an available port, avoiding conflicts with
    the FastAPI server running on port 8000.
    """
    print(
        "\nA browser window will open for Gmail consent.\n"
        "Log in with the Google account whose emails you want to read.\n"
    )
    creds = flow.run_local_server(port=0)

    if not creds.refresh_token:
        print(
            "WARNING: No refresh token received. This can happen if you previously\n"
            "granted access without 'prompt=consent'. Revoke access at\n"
            "  https://myaccount.google.com/permissions\n"
            "then re-run this script."
        )
        return

    keyring.set_password(
        kr["service_name"],
        kr["gmail_token_key_name"],
        creds.refresh_token,
    )
    print("Gmail refresh token stored in keyring.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    print("=" * 60)
    print("email Email Agent — First-Time Secret Setup")
    print("=" * 60)

    kr = _load_keyring_config()
    setup_openai_key(kr)
    setup_gmail_credentials(kr)

    print(
        "\n" + "=" * 60 + "\n"
        "Setup complete.\n\n"
        "To start the server:\n"
        "  python -m uvicorn app.main:app --reload\n"
        "  (from the backend/ directory)\n"
        "=" * 60
    )


if __name__ == "__main__":
    main()
