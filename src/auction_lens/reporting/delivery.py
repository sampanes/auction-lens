"""Sending a rendered report over SMTP.

Credentials are read from the environment by name so that a configuration file
can be committed and shared while the secrets stay on the machine that runs it.
"""

from __future__ import annotations

import os
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage

from ..config import EmailConfig, EmailSecurity
from ..models import Candidate
from .html import render_html
from .text import render_text

SMTP_TIMEOUT_SECONDS = 30
MATCH_COUNT_PLACEHOLDER = "{{ match_count }}"


@dataclass(frozen=True)
class MailAccount:
    """The five values an SMTP submission needs, once resolved."""

    host: str
    username: str
    password: str
    sender: str
    recipient: str


def send_email(candidates: list[Candidate], config: EmailConfig) -> None:
    """Send one report as a text message with an HTML alternative."""
    account = _account_from_environment(config)
    message = _build_message(candidates, config, account)

    is_implicit_tls = config.security == EmailSecurity.SSL
    transport = smtplib.SMTP_SSL if is_implicit_tls else smtplib.SMTP
    with transport(account.host, config.port, timeout=SMTP_TIMEOUT_SECONDS) as smtp:
        if config.security == EmailSecurity.STARTTLS:
            smtp.starttls()
        smtp.login(account.username, account.password)
        smtp.send_message(message)


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
) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = config.subject.replace(MATCH_COUNT_PLACEHOLDER, str(len(candidates)))
    message["From"] = account.sender
    message["To"] = account.recipient
    message.set_content(render_text(candidates))
    message.add_alternative(render_html(candidates), subtype="html")
    return message
