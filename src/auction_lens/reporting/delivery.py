"""Sending a rendered report over SMTP.

Credentials are read from the environment by name so that a configuration file
can be committed and shared while the secrets stay on the machine that runs it.
"""

from __future__ import annotations

import os
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from zoneinfo import ZoneInfo

from ..config import EmailConfig, EmailSecurity
from ..models import Candidate, WatchedItem
from .html import render_html
from .text import render_text
from .watchlist import render_watchlist, render_watchlist_html

SMTP_TIMEOUT_SECONDS = 30
MATCH_COUNT_PLACEHOLDER = "{{ match_count }}"
WATCHLIST_SUBJECT = "Auction Lens watchlist: {selection}"


@dataclass(frozen=True)
class MailAccount:
    """The five values an SMTP submission needs, once resolved."""

    host: str
    username: str
    password: str
    sender: str
    recipient: str


def check_email_ready(config: EmailConfig) -> None:
    """Validate local email settings without connecting or sending anything."""
    _ready_account(config)


def _ready_account(config: EmailConfig) -> MailAccount:
    """Resolve a usable account once every public sending boundary is safe."""
    if not config.enabled:
        raise RuntimeError("email reporting is disabled in the selected configuration")
    _require_secure_transport(config)
    return _account_from_environment(config)


def send_email(
    candidates: list[Candidate], config: EmailConfig, zone: ZoneInfo
) -> None:
    """Send one report as a text message with an HTML alternative."""
    account = _ready_account(config)
    message = _build_message(candidates, config, account, zone)

    _deliver(message, config, account)


def send_watchlist_email(items: tuple[WatchedItem, ...], config: EmailConfig) -> None:
    """Send selected lots without exposing the local watchlist path."""
    account = _ready_account(config)
    message = EmailMessage()
    message["Subject"] = WATCHLIST_SUBJECT.format(selection=_selection(len(items)))
    message["From"] = account.sender
    message["To"] = account.recipient
    message.set_content(render_watchlist(items))
    message.add_alternative(render_watchlist_html(items), subtype="html")
    _deliver(message, config, account)


def _selection(count: int) -> str:
    noun = "lot" if count == 1 else "lots"
    return f"{count} selected {noun}"


def _deliver(message: EmailMessage, config: EmailConfig, account: MailAccount) -> None:
    """Submit one already-built message through the configured secure transport."""
    _require_secure_transport(config)
    tls_context = ssl.create_default_context()
    is_implicit_tls = config.security == EmailSecurity.SSL
    if is_implicit_tls:
        connection = smtplib.SMTP_SSL(
            account.host,
            config.port,
            timeout=SMTP_TIMEOUT_SECONDS,
            context=tls_context,
        )
    else:
        connection = smtplib.SMTP(
            account.host,
            config.port,
            timeout=SMTP_TIMEOUT_SECONDS,
        )
    with connection as smtp:
        if not is_implicit_tls:
            smtp.starttls(context=tls_context)
        smtp.login(account.username, account.password)
        smtp.send_message(message)


def _require_secure_transport(config: EmailConfig) -> None:
    """Fail closed if a caller bypassed the typed configuration boundary."""
    if config.security not in (EmailSecurity.SSL, EmailSecurity.STARTTLS):
        raise ValueError("security must be one of: ssl, starttls")


def _account_from_environment(config: EmailConfig) -> MailAccount:
    """Resolve each configured variable name, naming all that are missing at once."""
    values = {
        "host": os.getenv(config.host_env),
        "username": os.getenv(config.username_env),
        "password": os.getenv(config.password_env),
        "sender": os.getenv(config.sender_env),
        "recipient": os.getenv(config.recipient_env),
    }
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise RuntimeError(f"missing email environment settings: {', '.join(missing)}")
    return MailAccount(**values)


def _build_message(
    candidates: list[Candidate],
    config: EmailConfig,
    account: MailAccount,
    zone: ZoneInfo,
) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = config.subject.replace(MATCH_COUNT_PLACEHOLDER, str(len(candidates)))
    message["From"] = account.sender
    message["To"] = account.recipient
    message.set_content(render_text(candidates, zone))
    message.add_alternative(render_html(candidates, zone), subtype="html")
    return message
