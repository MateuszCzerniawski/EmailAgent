"""
LLMResponse model — structured output produced by the LangChain analysis chain.

All four analysis dimensions (summary, category, sentiment, action detection) are
returned by a *single* LLM call. This keeps cost low and maximises SQLiteCache
effectiveness, because there is only one cache key per email instead of four.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pydantic import BaseModel, Field, field_validator


class ActionItem(BaseModel):
    task: str = Field(
        description="A clear, actionable description of the task to be performed."
    )
    deadline: str | None = Field(
        default=None,
        description="Proposed deadline or due date explicitly mentioned or inferred from the email content. "
                    "Return as a plain string (e.g. 'Friday', 'end of month', '2025-06-01'). Return null if no date is mentioned."
    )


class LLMResponse(BaseModel):
    """Immutable structured result of LLM email analysis."""

    model_config = {"frozen": True}

    # --- Source reference ---
    email_id: str = Field(description="ID of the Mail that was analysed")

    # --- Analysis output ---
    summary: str = Field(
        description="Shrotened plain-language summary of the email content, length relative to whole email but "
                    "maximum 5 sentences."
    )
    brief_summary: str = Field(
        default="",
        description="Two to four sentences ar keywords summarising the topic to minimum")

    category: str = Field(
        description="One value from the configured email_categories list in config.yaml"
    )
    sentiment: str = Field(
        description="One of: positive, neutral, negative, urgent"
    )
    key_action_required: bool = Field(
        description="True if the email explicitly asks the recipient to do something"
    )
    # action_description: str | None = Field(
    #     default=None,
    #     description="What action is needed, populated only when key_action_required is True",
    # )
    actions: list[ActionItem] = Field(
        default_factory=list,
        description="List of specific actions/tasks recommended based on the email. Populate only if "
                    "key_action_required is True. Return an empty list if no action is required."
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Model self-reported confidence in category + sentiment (0.0–1.0)",
    )

    @field_validator('actions', mode='before')
    @classmethod
    def coerce_null_actions(cls, v):
        return v if v is not None else []

    # --- Cache metadata ---
    cached: bool = Field(
        default=False,
        description="True if this response was served from SQLiteCache (not a live API call)",
    )
    analyzed_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp of when analysis was run or cache was hit",
    )
    tags: list[str] = Field(
        default_factory=list,
        description=(
            "3–7 short lowercase tags categorising this email. "
            "Examples: invoice, meeting-request, deadline, follow-up, "
            "job-offer, legal, contract, feedback, onboarding, alert."
        )
    )


class ThreadLLMResponse(BaseModel):
    """Immutable structured result of LLM analysis of an entire email thread."""

    model_config = {"frozen": True}

    # --- Source reference ---
    thread_id: str = Field(description="ID of the thread that was analysed")
    message_count: int = Field(description="Number of messages in the thread")

    # --- Analysis output ---
    summary: str = Field(
        description="Shrotened plain-language summary of the email content, length relative to whole email"
    )
    brief_summary: str = Field(
        default="",
        description="Two to four sentences ar keywords summarising the topic to minimum")

    category: str = Field(
        description="One value from the configured email_categories list in config.yaml"
    )
    sentiment: str = Field(
        description="One of: positive, neutral, negative, urgent"
    )
    key_action_required: bool = Field(
        description="True if the thread explicitly asks the recipient to do something"
    )
    # action_description: str | None = Field(
    #     default=None,
    #     description="What action is needed, populated only when key_action_required is True",
    # )
    actions: list[ActionItem] = Field(
        default_factory=list,
        description="List of specific actions/tasks recommended based on the email. Populate only if "
                    "key_action_required is True. Return an empty list if no action is required."
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Model self-reported confidence in category + sentiment (0.0–1.0)",
    )

    @field_validator('actions', mode='before')
    @classmethod
    def coerce_null_actions(cls, v):
        return v if v is not None else []

    # --- Cache metadata ---
    cached: bool = Field(
        default=False,
        description="True if this response was served from cache (not a live API call)",
    )
    analyzed_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp of when analysis was run or cache was hit",
    )
    tags: list[str] = Field(
        default_factory=list,
        description=(
            "3–7 short lowercase tags categorising this email. "
            "Examples: invoice, meeting-request, deadline, follow-up, "
            "job-offer, legal, contract, feedback, onboarding, alert."
        )
    )
