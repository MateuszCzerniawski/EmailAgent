"""
GmailPostbox — concrete Gmail implementation of the Postbox ABC.

Authentication flow
-------------------
1. Client credentials (client_id, client_secret) are stored in the OS keyring
   by `scripts/setup_secrets.py` during first-time setup.
2. `connect()` loads the refresh token from keyring, builds a
   `google.oauth2.credentials.Credentials` object, refreshes the access token,
   and initialises the Gmail API service.
3. The Gmail service object is valid for the lifetime of this instance.
4. `disconnect()` nullifies the service object.

Scopes
------
Only `gmail.readonly` is requested — the server never modifies the mailbox.

Body decoding
-------------
Gmail stores message bodies as base64url-encoded strings. This module decodes
them and prefers plain-text parts; HTML is kept as a fallback.
"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import keyring
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.config import AppConfig
from app.logger import get_logger
from app.models.mail import Mail
from app.postbox.base import EmailNotFoundError, Postbox, PostboxAuthError, ThreadNotFoundError

logger = get_logger(__name__)

# Gmail OAuth scopes — readonly for fetching, send for outbound test emails.
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]

# Token endpoint used when refreshing an access token.
_TOKEN_URI = "https://oauth2.googleapis.com/token"


class GmailPostbox(Postbox):
    """
    Gmail implementation of Postbox.

    Credentials lifecycle:
      1. setup_secrets.py stores client_id, client_secret, and refresh_token
         in the OS keyring under the configured service_name.
      2. connect() reads those three values from keyring, builds Credentials,
         and eagerly refreshes the access token.
      3. The googleapiclient service is then available for API calls.
    """

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._service: Any = None  # set by connect()

    # ------------------------------------------------------------------
    # Postbox interface
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """Load keyring credentials, refresh the access token, build service."""
        kr = self._config.keyring
        refresh_token = keyring.get_password(kr.service_name, kr.gmail_token_key_name)
        client_id = keyring.get_password(kr.service_name, kr.gmail_client_id_key_name)
        client_secret = keyring.get_password(kr.service_name, kr.gmail_client_secret_key_name)

        if not refresh_token:
            raise PostboxAuthError(
                "Gmail refresh token not found in keyring.\n"
                "Run:  python -m uv run setup-secrets\n"
                "Or navigate to GET /auth/gmail to start the OAuth flow."
            )
        if not client_id or not client_secret:
            raise PostboxAuthError(
                "Gmail client_id / client_secret not found in keyring.\n"
                "Run:  python -m uv run setup-secrets"
            )

        creds = Credentials(
            token=None,  # will be populated on first refresh
            refresh_token=refresh_token,
            token_uri=_TOKEN_URI,
            client_id=client_id,
            client_secret=client_secret,
            scopes=SCOPES,
        )

        # Eagerly refresh so we surface auth errors before the first API call.
        try:
            creds.refresh(Request())
        except RefreshError as exc:
            raise PostboxAuthError(
                f"Failed to refresh Gmail access token: {exc}\n"
                "The refresh token may have been revoked. Re-run setup-secrets."
            ) from exc

        self._service = build("gmail", "v1", credentials=creds)
        logger.info("GmailPostbox connected (user: me)")

    def fetch_emails(
        self,
        max_results: int = 50,
        query: str = "",
    ) -> list[Mail]:
        """
        List emails matching *query*. Returns headers + snippet only (no body).

        Two API calls are made per batch:
          1. messages.list() — returns IDs.
          2. messages.get(format='metadata') — returns headers + snippet per ID.
        """
        self._ensure_connected()

        try:
            response = (
                self._service.users()
                .messages()
                .list(userId="me", maxResults=max_results, q=query)
                .execute()
            )
        except HttpError as exc:
            logger.error("Gmail messages.list failed: %s", exc)
            raise

        messages = response.get("messages", [])
        if not messages:
            return []

        mails: list[Mail] = []
        for msg_stub in messages:
            try:
                raw = (
                    self._service.users()
                    .messages()
                    .get(userId="me", id=msg_stub["id"], format="metadata")
                    .execute()
                )
                mails.append(self._parse_metadata(raw))
            except HttpError as exc:
                # Skip individual failures rather than aborting the entire list.
                logger.warning("Failed to fetch message %s: %s", msg_stub["id"], exc)

        return mails

    def get_email_metadata(self, email_id: str) -> Mail:
        """Fetch a single email with headers only (lightweight, no body)."""
        self._ensure_connected()
        try:
            raw = (
                self._service.users()
                .messages()
                .get(userId="me", id=email_id, format="metadata")
                .execute()
            )
        except HttpError as exc:
            if exc.resp.status == 404:
                raise EmailNotFoundError(f"Email not found: {email_id}") from exc
            raise
        return self._parse_metadata(raw)

    def get_email(self, email_id: str) -> Mail:
        """Fetch a single email with full body (plain-text preferred)."""
        self._ensure_connected()

        try:
            raw = (
                self._service.users()
                .messages()
                .get(userId="me", id=email_id, format="full")
                .execute()
            )
        except HttpError as exc:
            if exc.resp.status == 404:
                raise EmailNotFoundError(f"Email not found: {email_id}") from exc
            raise

        return self._parse_full(raw)

    def get_thread(self, thread_id: str) -> list[Mail]:
        """Fetch all messages in a thread with full bodies (plain-text preferred)."""
        self._ensure_connected()

        try:
            raw = (
                self._service.users()
                .threads()
                .get(userId="me", id=thread_id, format="full")
                .execute()
            )
        except HttpError as exc:
            if exc.resp.status == 404:
                raise ThreadNotFoundError(f"Thread not found: {thread_id}") from exc
            raise

        return [self._parse_full(msg) for msg in raw.get("messages", [])]

    def get_thread_message_count(self, thread_id: str) -> int:
        """Return the total number of messages in a Gmail thread (including sent)."""
        self._ensure_connected()
        try:
            raw = (
                self._service.users()
                .threads()
                .get(userId="me", id=thread_id, format="minimal")
                .execute()
            )
        except HttpError as exc:
            if exc.resp.status == 404:
                raise ThreadNotFoundError(f"Thread not found: {thread_id}") from exc
            raise
        return len(raw.get("messages", []))

    def send_email(self, to: str, subject: str, body: str, thread_id: str | None = None, in_reply_to: str | None = None) -> str:
        """
        Send a plain-text email via the Gmail API.

        Returns the Gmail message ID of the sent message.
        Requires the gmail.send scope — re-authenticate if the stored token
        was issued with gmail.readonly only.
        """
        from email.mime.text import MIMEText

        self._ensure_connected()

        msg = MIMEText(body, "plain", "utf-8")
        msg["To"] = to
        msg["Subject"] = subject
        if in_reply_to:
            msg["In-Reply-To"] = in_reply_to
            msg["References"] = in_reply_to

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        message_body: dict = {"raw": raw}
        if thread_id:
            message_body["threadId"] = thread_id
        result = (
            self._service.users()
            .messages()
            .send(userId="me", body=message_body)
            .execute()
        )
        logger.info("Email sent to %s (message id: %s)", to, result.get("id"))
        return result.get("id", "")

    def disconnect(self) -> None:
        """Release the Gmail service object."""
        self._service = None
        logger.debug("GmailPostbox disconnected")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ensure_connected(self) -> None:
        if self._service is None:
            raise PostboxAuthError(
                "GmailPostbox.connect() must be called before making API requests."
            )

    @staticmethod
    def _extract_header(headers: list[dict[str, str]], name: str) -> str:
        """Case-insensitive lookup for a Gmail message header value."""
        name_lower = name.lower()
        for h in headers:
            if h.get("name", "").lower() == name_lower:
                return h.get("value", "")
        return ""

    @staticmethod
    def _decode_base64url(data: str) -> str:
        """Decode a base64url-encoded Gmail body part to a UTF-8 string."""
        # Pad to a multiple of 4 to satisfy the decoder.
        padded = data + "=" * (4 - len(data) % 4)
        return base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")

    def _parse_metadata(self, raw: dict[str, Any]) -> Mail:
        """Build a Mail from a Gmail metadata-format message (no body)."""
        headers = raw.get("payload", {}).get("headers", [])
        labels = raw.get("labelIds", [])

        date_str = self._extract_header(headers, "Date")
        try:
            date = parsedate_to_datetime(date_str).astimezone(timezone.utc)
        except Exception:
            date = datetime.now(timezone.utc)

        to_header = self._extract_header(headers, "To")
        recipients = [addr.strip() for addr in to_header.split(",") if addr.strip()]

        return Mail(
            id=raw["id"],
            thread_id=raw.get("threadId", ""),
            subject=self._extract_header(headers, "Subject") or "(no subject)",
            sender=self._extract_header(headers, "From"),
            recipients=recipients,
            date=date,
            snippet=raw.get("snippet", ""),
            labels=labels,
            has_attachments=self._has_attachments(raw.get("payload", {})),
        )

    def _parse_full(self, raw: dict[str, Any]) -> Mail:
        """Build a Mail from a Gmail full-format message (includes body)."""
        payload = raw.get("payload", {})
        headers = payload.get("headers", [])
        labels = raw.get("labelIds", [])

        date_str = self._extract_header(headers, "Date")
        try:
            date = parsedate_to_datetime(date_str).astimezone(timezone.utc)
        except Exception:
            date = datetime.now(timezone.utc)

        to_header = self._extract_header(headers, "To")
        recipients = [addr.strip() for addr in to_header.split(",") if addr.strip()]

        body_plain, body_html = self._extract_bodies(payload)

        return Mail(
            id=raw["id"],
            thread_id=raw.get("threadId", ""),
            subject=self._extract_header(headers, "Subject") or "(no subject)",
            sender=self._extract_header(headers, "From"),
            recipients=recipients,
            date=date,
            snippet=raw.get("snippet", ""),
            body_plain=body_plain,
            body_html=body_html,
            labels=labels,
            has_attachments=self._has_attachments(payload),
            rfc_message_id=self._extract_header(headers, "Message-ID") or None,
        )

    def _extract_bodies(
        self, payload: dict[str, Any]
    ) -> tuple[str | None, str | None]:
        """
        Recursively walk MIME parts to extract plain-text and HTML bodies.

        Returns:
            (body_plain, body_html) — either may be None if not present.
        """
        mime_type = payload.get("mimeType", "")
        body_data = payload.get("body", {}).get("data", "")

        if mime_type == "text/plain" and body_data:
            return self._decode_base64url(body_data), None
        if mime_type == "text/html" and body_data:
            return None, self._decode_base64url(body_data)

        # Recurse into multipart/* parts.
        plain: str | None = None
        html: str | None = None
        for part in payload.get("parts", []):
            p, h = self._extract_bodies(part)
            plain = plain or p
            html = html or h

        return plain, html

    @staticmethod
    def _has_attachments(payload: dict[str, Any]) -> bool:
        """Return True if any MIME part has a non-empty filename (attachment)."""
        body = payload.get("body", {})
        if payload.get("filename") and body.get("size", 0) > 0:
            return True
        for part in payload.get("parts", []):
            if GmailPostbox._has_attachments(part):
                return True
        return False
