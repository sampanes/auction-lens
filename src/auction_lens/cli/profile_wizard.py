"""Preview, confirm, and save the small profile edits the CLI understands.

The configuration editor owns TOML preservation and atomic snapshots. This
module owns only the conversation: three practical questions, a plain-language
preview, an exact zero-context diff, and an explicit confirmation.
"""

from __future__ import annotations

import sys
from decimal import Decimal
from difflib import unified_diff
from pathlib import Path

from ..config import AppConfig, LargeItemPolicy, LogisticsConfig, parse_config, render_profile
from ..config.editor import (
    REMOVE,
    ProfileEdits,
    profile_snapshot_path,
    recover_profile_restore,
    restore_profile_text,
    save_profile_text,
    update_profile_text,
)
from ..fields import parse_decimal

CANCELLED = "Cancelled; no files changed."
NO_CHANGES = "No changes requested; no files changed."
RECOVERED = "Recovered an interrupted profile restore before continuing."


def edit_profile(path: str | Path) -> None:
    """Ask for three stable logistics choices and save only after a preview."""
    _require_interactive_terminal()
    config_path = Path(path)
    _prepare_profile(config_path)
    current_bytes = config_path.read_bytes()
    current_text = _text(current_bytes)
    current = parse_config(current_text)

    try:
        edits = _ask_for_logistics(current.logistics)
    except (EOFError, KeyboardInterrupt):
        _cancel()
        return

    if edits == ProfileEdits():
        print(NO_CHANGES)
        return

    proposed_text = update_profile_text(current_text, edits)
    proposed_bytes = proposed_text.encode("utf-8")
    if proposed_bytes == current_bytes:
        print(NO_CHANGES)
        return

    proposed = parse_config(proposed_text)
    _show_preview(current_text, proposed_text, proposed)
    try:
        confirmed = _confirmed("Save these changes?")
    except (EOFError, KeyboardInterrupt):
        _cancel()
        return
    if not confirmed:
        print(CANCELLED)
        return

    snapshot = save_profile_text(config_path, current_bytes, proposed_bytes)
    print("Saved.")
    print(f"Rollback snapshot: {snapshot}")
    print("Restore it with profile --restore and the same --config path.")


def restore_profile(path: str | Path) -> None:
    """Preview the last snapshot and swap it with the current configuration."""
    _require_interactive_terminal()
    config_path = Path(path)
    _prepare_profile(config_path)
    current_bytes = config_path.read_bytes()
    snapshot = profile_snapshot_path(config_path)
    try:
        previous_bytes = snapshot.read_bytes()
    except FileNotFoundError as error:
        raise ValueError(
            f"{snapshot}: no profile snapshot to restore; run profile --edit first"
        ) from error
    current_text = _text(current_bytes)
    previous_text = _text(previous_bytes)

    # A restore is also a configuration write. Validate what would become
    # current, while still allowing it to rescue a current file edited badly.
    proposed = parse_config(previous_text)
    if previous_bytes == current_bytes:
        print(NO_CHANGES)
        return

    _show_preview(current_text, previous_text, proposed)
    try:
        confirmed = _confirmed("Restore this configuration?")
    except (EOFError, KeyboardInterrupt):
        _cancel()
        return
    if not confirmed:
        print(CANCELLED)
        return

    restore_profile_text(config_path, current_bytes, previous_bytes)
    print("Restored. Run profile --restore again to swap back.")


def _ask_for_logistics(logistics: LogisticsConfig) -> ProfileEdits:
    print("Press Enter to keep a current value.")
    print("Type default to remove that setting and use the built-in default.")
    print()
    print("Large-item choices:")
    print("  ask    keep the lot eligible and ask for a handling plan")
    print("  allow  treat the lot like any other")
    print("  reject leave the lot out")

    policy = _policy_answer(logistics.large_item_policy)
    weight = _decimal_answer(
        "Weight over which a lot counts as large, in lb",
        logistics.manual_handling_limit_lb,
    )
    dimension = _decimal_answer(
        "Dimension over which a lot counts as large, in inches",
        logistics.large_dimension_threshold_in,
    )
    return ProfileEdits(
        large_item_policy=policy,
        manual_handling_limit_lb=weight,
        large_dimension_threshold_in=dimension,
    )


def _policy_answer(current: LargeItemPolicy):
    while True:
        answer = input(
            f"Large-item policy (ask, allow, or reject) [{current.value}]: "
        ).strip().casefold()
        if not answer:
            return None
        if answer == "default":
            return REMOVE
        try:
            chosen = LargeItemPolicy(answer)
        except ValueError:
            print("    Choose ask, allow, or reject.")
            continue
        return None if chosen == current else chosen


def _decimal_answer(question: str, current: Decimal):
    while True:
        answer = input(f"{question} [{current}]: ").strip()
        if not answer:
            return None
        if answer.casefold() == "default":
            return REMOVE
        try:
            chosen = parse_decimal(answer, field_name=question)
        except ValueError:
            print("    Enter a non-negative finite number.")
            continue
        return None if chosen == current else chosen


def _show_preview(
    current_text: str, proposed_text: str, proposed: AppConfig
) -> None:
    print()
    print("PROPOSED PROFILE")
    print(render_profile(proposed), end="")
    print()
    print("EXACT CONFIGURATION DIFF")
    print(_configuration_diff(current_text, proposed_text), end="")


def _configuration_diff(current_text: str, proposed_text: str) -> str:
    diff_lines = list(unified_diff(
        current_text.splitlines(),
        proposed_text.splitlines(),
        fromfile="current configuration",
        tofile="proposed configuration",
        n=0,
        lineterm="",
    ))
    sections = ["\n".join(diff_lines)] if diff_lines else []
    current_endings = _line_ending_signature(current_text)
    proposed_endings = _line_ending_signature(proposed_text)
    if current_endings != proposed_endings:
        sections.append(
            "LINE-ENDING BYTES\n"
            f"current:  {_summarize_endings(current_endings)}\n"
            f"proposed: {_summarize_endings(proposed_endings)}"
        )
    return "\n\n".join(sections) + "\n"


def _line_ending_signature(text: str) -> tuple[str, ...]:
    signature = []
    for line in text.splitlines(keepends=True):
        if line.endswith("\r\n"):
            signature.append("CRLF")
        elif line.endswith("\n"):
            signature.append("LF")
        elif line.endswith("\r"):
            signature.append("CR")
        else:
            signature.append("no terminator")
    return tuple(signature)


def _summarize_endings(signature: tuple[str, ...]) -> str:
    if not signature:
        return "no lines"
    runs = []
    start = 1
    for position in range(2, len(signature) + 2):
        if position <= len(signature) and signature[position - 1] == signature[start - 1]:
            continue
        location = f"line {start}" if start == position - 1 else f"lines {start}-{position - 1}"
        runs.append(f"{location} {signature[start - 1]}")
        start = position
    return "; ".join(runs)


def _confirmed(question: str) -> bool:
    answer = input(f"{question} Type yes or y to continue: ")
    return answer.strip().casefold() in {"yes", "y"}


def _require_interactive_terminal() -> None:
    if not sys.stdin.isatty():
        raise RuntimeError("profile changes require an interactive terminal")


def _prepare_profile(path: Path) -> None:
    # Resolving a known interrupted swap is safer than presenting stale bytes.
    if recover_profile_restore(path):
        print(RECOVERED)


def _text(contents: bytes) -> str:
    try:
        return contents.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("profile configuration must be UTF-8 text") from error


def _cancel() -> None:
    print()
    print(CANCELLED)
