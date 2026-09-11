"""Asking a person a question at a terminal and getting a usable answer back.

Nothing here knows what any answer is for. That is the point: the one place in
this program that handles a password should be small enough to read in full,
and it should be obvious from the file that it never stores, prints, or logs
what it was given.
"""

from __future__ import annotations

import sys
import warnings
from getpass import GetPassWarning, getpass


def require_interactive_terminal() -> None:
    """Never fall back to a password prompt that may echo its input."""
    if not sys.stdin.isatty():
        raise RuntimeError(
            "mail setup needs an interactive terminal so the password stays hidden"
        )


def ask(question: str, default: str) -> str:
    """One line of input, where pressing Enter accepts the suggestion."""
    shown = f"{question} [{default}]: " if default else f"{question}: "
    return input(shown).strip() or default


def ask_until_answered(question: str, default: str) -> str:
    """A non-empty one-line answer, with an optional suggested value."""
    while True:
        answer = ask(question, default)
        if answer:
            return answer
        print("    Nothing entered. Try again.")


def ask_for_address(question: str, default: str) -> str:
    """An email address, checked only for the shape every host agrees on."""
    while True:
        answer = ask(question, default)
        if looks_like_address(answer):
            return answer
        print("    That is not an email address. Try again.")


def looks_like_address(value: str) -> bool:
    """Whether a value has the small amount of structure SMTP always needs."""
    return value.count("@") == 1 and all(part.strip() for part in value.split("@"))


def ask_for_secret(question: str, *, remove_display_spaces: bool) -> str:
    """Read a password without echoing it or silently changing provider data.

    Google prints an app password in four groups of four and people paste it
    exactly as shown. Only the Gmail host opts into removing that display
    formatting; another provider may legitimately use spaces in a password.
    """
    while True:
        with warnings.catch_warnings():
            warnings.simplefilter("error", GetPassWarning)
            try:
                typed = getpass(f"{question}: ")
            except GetPassWarning as error:
                raise RuntimeError(
                    "secure password input is unavailable in this terminal"
                ) from error
            except (EOFError, KeyboardInterrupt) as error:
                raise RuntimeError(
                    "mail setup stopped before a password was entered"
                ) from error
        if remove_display_spaces:
            typed = "".join(typed.split())
        if typed:
            return typed
        print("    Nothing entered. Try again.")
