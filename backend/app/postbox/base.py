"""
Postbox — abstract base class for email provider integrations.

All concrete implementations (GmailPostbox, OutlookPostbox, …) must inherit
from this class and implement every abstract method. This contract lets the
rest of the application stay provider-agnostic.

I/O note: Gmail API calls are synchronous (blocking). FastAPI routes that call
these methods should be defined as plain `def` functions — FastAPI automatically
runs synchronous route handlers in a thread pool, preventing event-loop blocking.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from app.models.mail import Mail


class PostboxAuthError(Exception):
    """Raised when authentication credentials are missing or invalid."""


class EmailNotFoundError(Exception):
    """Raised when the requested email ID does not exist in the mailbox."""


class ThreadNotFoundError(Exception):
    """Raised when the requested thread ID does not exist in the mailbox."""


class Postbox(ABC):
    """
    Abstract base class for all email provider integrations.

    Concrete implementations handle authentication internally. The lifecycle is:
        1. Instantiate with provider-specific config.
        2. Call connect() (or use as a context manager).
        3. Call fetch_emails() / get_email() as needed.
        4. Call disconnect() when done.
    """

    @abstractmethod
    def connect(self) -> None:
        """
        Establish an authenticated session to the mail provider.

        Must be called before fetch_emails() or get_email().

        Raises:
            PostboxAuthError: If credentials are missing or the token is invalid.
        """

    @abstractmethod
    def fetch_emails(
        self,
        max_results: int = 50,
        query: str = "",
    ) -> list[Mail]:
        """
        Retrieve a list of emails matching the query.

        Returns Mail objects with headers and snippet populated, but
        body_plain and body_html are None (full body requires get_email()).

        Args:
            max_results: Maximum number of messages to return.
            query: Provider-specific filter string (e.g. Gmail query syntax).

        Returns:
            List of Mail objects ordered newest-first.
        """

    @abstractmethod
    def get_email(self, email_id: str) -> Mail:
        """
        Retrieve a single email by its provider-assigned ID, including full body.

        Args:
            email_id: Provider-assigned unique message identifier.

        Returns:
            Mail object with body_plain (and optionally body_html) populated.

        Raises:
            EmailNotFoundError: If no message with this ID exists.
        """

    @abstractmethod
    def get_thread(self, thread_id: str) -> list[Mail]:
        """
        Retrieve all messages in a thread by its provider-assigned thread ID.

        Args:
            thread_id: Provider-assigned unique thread identifier.

        Returns:
            List of Mail objects in the thread, ordered oldest-first.

        Raises:
            ThreadNotFoundError: If no thread with this ID exists.
        """

    @abstractmethod
    def disconnect(self) -> None:
        """
        Close the authenticated session and release resources.

        Safe to call even if connect() was never called.
        """

    # ------------------------------------------------------------------
    # Context manager support — lets routes use:  with postbox: ...
    # ------------------------------------------------------------------

    def __enter__(self) -> "Postbox":
        self.connect()
        return self

    def __exit__(self, *args: Any) -> None:
        self.disconnect()
