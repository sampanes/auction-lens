"""Report length, reading order, and configured delivery channels."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from ..matching.model import ReadingOrder
from ..values import require_at_least, require_within, settle_choice

HIGHEST_PORT = 65535


class EmailSecurity(StrEnum):
    """How the SMTP connection is protected."""

    SSL = "ssl"
    STARTTLS = "starttls"


@dataclass(frozen=True)
class EmailConfig:
    """Where a report is sent, and which variables hold the secrets."""

    enabled: bool = False
    host_env: str = "AUCTION_LENS_SMTP_HOST"
    port: int = 465
    security: EmailSecurity = EmailSecurity.SSL
    username_env: str = "AUCTION_LENS_SMTP_USERNAME"
    password_env: str = "AUCTION_LENS_SMTP_PASSWORD"
    sender_env: str = "AUCTION_LENS_EMAIL_FROM"
    recipient_env: str = "AUCTION_LENS_EMAIL_TO"
    subject: str = "Auction Lens report"

    def __post_init__(self) -> None:
        settle_choice(self, "security", EmailSecurity)
        require_within(self.port, low=1, high=HIGHEST_PORT, field_name="port")


@dataclass(frozen=True)
class WebhookConfig:
    """Where a compact report is posted and what it may say."""

    enabled: bool = False
    url_env: str = "AUCTION_LENS_WEBHOOK_URL"
    max_items: int = 10
    username: str = "Auction Lens"

    def __post_init__(self) -> None:
        require_at_least(self.max_items, 1, field_name="max_items")


# Enough of one interest to see today's crop while leaving room for others.
DEFAULT_MOST_PER_INTEREST = 3


@dataclass(frozen=True)
class ReportsConfig:
    """How much of the ranking is worth putting in front of a person."""

    max_items: int | None = None
    # Reading order changes only what a person sees first, never admission.
    order: ReadingOrder = ReadingOrder.PRIORITY
    most_per_interest: int = DEFAULT_MOST_PER_INTEREST

    def __post_init__(self) -> None:
        settle_choice(self, "order", ReadingOrder)
        if self.max_items is not None:
            require_at_least(self.max_items, 1, field_name="max_items")
        require_at_least(self.most_per_interest, 1, field_name="most_per_interest")
