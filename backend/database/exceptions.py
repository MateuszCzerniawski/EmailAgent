"""Custom exceptions for the database layer."""

from __future__ import annotations


class DuplicateEmailError(Exception):
    """Raised when insert_email() is called with a message_id that already exists."""

    def __init__(self, message_id: str) -> None:
        super().__init__(f"Email with message_id '{message_id}' already exists")
        self.message_id = message_id


class DatabaseConnectionError(Exception):
    """Raised when a database connection cannot be established."""


class EmbeddingError(Exception):
    """Raised when embedding generation fails."""
