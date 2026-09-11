"""First-run commands: the two files a fresh clone cannot carry, and the profile.

A public repository cannot hold what a person wants or what their password is,
so a new machine starts unable to run. These commands are the cure, and they
are the only ones that write to configuration rather than reading it.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from ..config import EmailConfig, load_config, render_profile
from ..env_file import write_settings
from .exit_codes import SUCCESS
from .parser import DEFAULT_CONFIG, DEFAULT_INBOX, EXAMPLE_CONFIG, PROGRAM
from .profile_wizard import edit_profile, restore_profile
from .prompts import (
    ask,
    ask_for_address,
    ask_for_secret,
    ask_until_answered,
    looks_like_address,
    require_interactive_terminal,
)

# The host most people setting this up are reaching for; compatible hosts are accepted.
DEFAULT_SMTP_HOST = "smtp.gmail.com"

ENV_TEMPLATE = """# Local settings for Auction Lens. Ignored by git; never commit it.

# Required before any request. The provider has to be able to tell who is
# asking, so this must contain a real contact address you control.
AUCTION_LENS_HTTP_USER_AGENT=

# Only needed if [reports.email] enabled = true in your configuration.
AUCTION_LENS_SMTP_HOST=
AUCTION_LENS_SMTP_USERNAME=
AUCTION_LENS_SMTP_PASSWORD=
AUCTION_LENS_EMAIL_FROM=
AUCTION_LENS_EMAIL_TO=

# Only needed if [reports.webhook] enabled = true. Treat it as a password:
# anyone holding this address can post into the channel.
AUCTION_LENS_WEBHOOK_URL=
"""


def setup(args: argparse.Namespace) -> int:
    """Create the two ignored files a fresh clone cannot carry, and say what to edit.

    Both are deliberately absent from git: one holds what a person wants, the
    other holds their secrets. A new machine therefore starts unable to run, and
    the only cure is a command that says so and fixes it.
    """
    config, env_file = Path(args.config), Path(args.env_file)
    print(_created(config, Path(EXAMPLE_CONFIG).read_text(encoding="utf-8")))
    print(_created(env_file, ENV_TEMPLATE))
    _report_required_edits(config, env_file)
    print()
    if config.suffix == ".toml":
        print(f"Review or change practical limits: {_profile_editor_command(config)}")
    else:
        print("Guided profile editing requires a configuration ending in .toml.")
    if args.email:
        return _ask_for_mail_settings(config, env_file)
    print(f"Then: {PROGRAM} daily")
    print(f"To be emailed the report: {PROGRAM} setup --email")
    return SUCCESS


def profile(args: argparse.Namespace) -> int:
    """Explain the stable operator choices without consulting any runtime state."""
    if args.edit:
        edit_profile(args.config)
        return SUCCESS
    if args.restore:
        restore_profile(args.config)
        return SUCCESS
    print(render_profile(load_config(args.config)), end="")
    return SUCCESS


def _created(path: Path, contents: str) -> str:
    """Write a starting file, and never overwrite one somebody has edited."""
    if path.exists():
        return f"[OK] {path} already exists, left alone"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents, encoding="utf-8")
    return f"[OK] wrote {path}"


def _report_required_edits(config: Path, env_file: Path) -> None:
    """Name the local decisions no public repository can safely make."""
    print()
    print("Before the first run, edit:")
    print(
        f"  {env_file}: identify your requests with a contact address you control in "
        "AUCTION_LENS_HTTP_USER_AGENT"
    )
    print(f"  {config}: confirm your authorization, locations, and interests")


def _profile_editor_command(config: Path) -> str:
    if config == Path(DEFAULT_CONFIG):
        return f"{PROGRAM} profile --edit"
    return f'{PROGRAM} profile --config "{config}" --edit'


def _ask_for_mail_settings(config: Path, env_file: Path) -> int:
    """Fill in the five mail variables, without the password ever being shown.

    The host is not allowlisted; its configured port and security mode still
    govern delivery. Provider-specific advice stays advice rather than turning
    this general setup command into a preference.
    """
    email = load_config(config).email
    require_interactive_terminal()
    print()
    host = ask("SMTP host", DEFAULT_SMTP_HOST)
    _mail_host_advice(host)
    username = ask_until_answered("SMTP username", "")
    sender_default = username if looks_like_address(username) else ""
    sender = ask_for_address("From address", sender_default)
    recipient = ask_for_address("Address they are sent to", sender)
    password = ask_for_secret(
        "Password or app password", remove_display_spaces=_is_gmail_host(host)
    )

    write_settings(
        env_file,
        {
            email.host_env: host,
            email.username_env: username,
            email.password_env: password,
            email.sender_env: sender,
            email.recipient_env: recipient,
        },
    )
    print()
    print(f"[OK] {env_file} updated. The password was neither printed nor logged.")
    return _report_email_switch(config, email)


def _mail_host_advice(host: str) -> None:
    """Say the one thing that host is known to need, without requiring it."""
    if _is_gmail_host(host):
        print("    Gmail needs 2-Step Verification and an app password, not the")
        print("    account password: https://myaccount.google.com/apppasswords")
        print("    See docs/GMAIL.md if that page offers you nothing.")


def _is_gmail_host(host: str) -> bool:
    """Identify the Gmail submission host narrowly enough to change a secret."""
    return host.strip().lower().rstrip(".") == DEFAULT_SMTP_HOST


def _report_email_switch(config: Path, email: EmailConfig) -> int:
    """Read the switch with the real loader rather than guessing at the file.

    Programmatically rewriting arbitrary TOML risks corrupting the configuration
    it was meant to help with, so this reports the one line to change and leaves
    the file to its owner.
    """
    if email.enabled:
        print(f"[OK] {config} already has [reports.email] enabled = true.")
        print(f"     Transport is {email.security.value} on port {email.port}.")
        print()
        print(f"Check local readiness: {PROGRAM} doctor --email")
        print(f"Send one now: {PROGRAM} run --input {DEFAULT_INBOX} --email")
        return SUCCESS
    print(f"[!] {config} still has [reports.email] enabled = false.")
    print("    Mail settings were saved, but delivery is not ready.")
    print("    Set it to true and confirm that port and security suit your SMTP host.")
    print()
    print(f"Then check it: {PROGRAM} doctor --email")
    return SUCCESS
