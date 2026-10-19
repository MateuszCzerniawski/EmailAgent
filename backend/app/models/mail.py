"""
Mail model — provider-agnostic representation of a single email message.

This Pydantic model is populated by any Postbox implementation and consumed
by the LLM service and API routes. It is intentionally decoupled from Gmail
specifics so that future providers (Outlook, IMAP, …) produce the same schema.
"""

from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, Field


class Mail(BaseModel):
    """Immutable, provider-agnostic email message."""

    model_config = {"frozen": True}

    # --- Identifiers ---
    id: str = Field(description="Provider-assigned unique message identifier")
    thread_id: str = Field(description="Thread / conversation identifier")

    # --- Headers ---
    subject: str = Field(default="(no subject)", description="Email subject line")
    sender: str = Field(description="From address, e.g. 'Name <addr@domain.com>'")
    recipients: list[str] = Field(
        default_factory=list,
        description="List of To: addresses",
    )
    date: datetime = Field(description="UTC datetime the message was sent")

    # --- Content ---
    snippet: str = Field(
        default="",
        description="Short preview text provided by the mail API (≤200 chars)",
    )
    body_plain: str | None = Field(
        default=None,
        description=(
            "Full plain-text body. None when only headers were fetched "
            "(e.g. from fetch_emails() — use get_email() to populate this field)."
        ),
    )
    body_html: str | None = Field(
        default=None,
        description="Full HTML body. None when only headers were fetched.",
    )

    # --- Reply threading ---
    rfc_message_id: str | None = Field(
        default=None,
        description="RFC 2822 Message-ID header value, e.g. '<CABcd...@mail.gmail.com>'. Used to set In-Reply-To when sending a reply.",
    )

    # --- Metadata ---
    labels: list[str] = Field(
        default_factory=list,
        description="Provider labels / folders (e.g. INBOX, UNREAD, CATEGORY_SOCIAL)",
    )
    has_attachments: bool = Field(
        default=False,
        description="True if the message contains at least one file attachment",
    )
