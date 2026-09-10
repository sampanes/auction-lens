"""Editing the small stable handling profile without rewriting its TOML.

The configuration remains a hand-readable source file.  This module therefore
changes only the three scalar values the guided profile owns, leaving comments,
ordering, line endings, and every unrelated byte alone.  Anything it cannot
locate unambiguously is refused rather than reformatted or guessed.
"""

from __future__ import annotations

import copy
import re
import tomllib
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path

from ..file_io import write_bytes_atomically
from .loader import parse_config
from .schema import LargeItemPolicy, LogisticsConfig

LOGISTICS_TABLE = "logistics"
EDITABLE_FIELDS = (
    "large_item_policy",
    "manual_handling_limit_lb",
    "large_dimension_threshold_in",
)

_POLICY_VALUE = re.compile(r'"(?:ask|allow|reject)"')
_NUMBER_VALUE = re.compile(r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?")
_LOGISTICS_HEADER = re.compile(r"[ \t]*\[logistics\][ \t]*(?:#.*)?")


class _Remove:
    """The explicit request to use a field's schema default again."""

    __slots__ = ()


REMOVE = _Remove()


@dataclass(frozen=True)
class ProfileEdits:
    """Optional handling answers; ``None`` means preserve the source exactly.

    ``REMOVE`` is deliberately different from ``None``.  It deletes an
    explicit assignment so the existing schema default becomes authoritative;
    this is what a guided answer of ``default`` means.
    """

    large_item_policy: LargeItemPolicy | str | _Remove | None = None
    manual_handling_limit_lb: Decimal | _Remove | None = None
    large_dimension_threshold_in: Decimal | _Remove | None = None


@dataclass(frozen=True)
class _TargetLine:
    index: int
    prefix: str
    suffix: str
    ending: str


@dataclass(frozen=True)
class _Layout:
    header: int | None
    end: int
    targets: dict[str, _TargetLine]


def update_profile_text(text: str, edits: ProfileEdits) -> str:
    """Return TOML carrying the requested handling edits, or fail unchanged.

    Existing values must use the ordinary one-line spelling emitted by the
    public example.  Quoted keys, dotted keys, inline tables, and multiline
    target values are valid TOML in some forms, but editing them safely would
    require a general TOML rewriter.  Refusing those uncommon forms keeps this
    narrow editor honest and preserves the file a person wrote.
    """
    current = parse_config(text)
    requested, proposed_logistics = _requested_edits(current.logistics, edits)
    if not requested:
        return text

    document = tomllib.loads(text, parse_float=Decimal)
    raw_logistics = document.get(LOGISTICS_TABLE)
    explicit = set(raw_logistics) if isinstance(raw_logistics, dict) else set()
    effective = {
        key: value
        for key, value in requested.items()
        if value is not REMOVE or key in explicit
    }
    if not effective:
        return text

    lines = text.splitlines(keepends=True)
    layout = _layout(lines)
    if raw_logistics is not None and layout.header is None:
        raise _unsupported()
    _require_canonical_targets(layout, explicit)

    line_changes: dict[int, str | None] = {}
    missing: dict[str, str] = {}
    for key in EDITABLE_FIELDS:
        if key not in effective:
            continue
        value = effective[key]
        target = layout.targets.get(key)
        if value is REMOVE:
            if target is not None:
                line_changes[target.index] = _comment_after_removal(target)
        elif target is None:
            missing[key] = value
        else:
            line_changes[target.index] = f"{target.prefix}{value}{target.suffix}{target.ending}"

    candidate = _change_existing_lines(text, lines, line_changes)
    if missing:
        candidate = _insert_missing(candidate, missing)

    try:
        proposed = parse_config(candidate)
        proposed_document = tomllib.loads(candidate, parse_float=Decimal)
    except ValueError as error:
        raise _unsupported() from error
    expected = replace(current, logistics=proposed_logistics)
    expected_document = _document_after_edits(document, effective, proposed_logistics)
    if proposed != expected or proposed_document != expected_document:
        raise _unsupported()
    return candidate


def profile_snapshot_path(path: str | Path) -> Path:
    """Place the one-step rollback beside the configuration it belongs to."""
    target = Path(path)
    _require_toml_path(target)
    return target.with_name(f"{target.name}.previous")


def profile_restore_recovery_path(path: str | Path) -> Path:
    """Name the ignored safety copy used only while swapping a snapshot."""
    target = Path(path)
    _require_toml_path(target)
    return target.with_name(f"{target.name}.previous.restore.tmp")


def recover_profile_restore(path: str | Path) -> bool:
    """Finish cleaning up, or roll back, an interrupted snapshot swap.

    A restore keeps the former current file in a third, ignored location until
    both halves of the swap are durable.  Most failures are rolled back inside
    the original call.  This function handles the smaller hard-stop window in
    which that cleanup could not run.
    """
    target = Path(path)
    snapshot = profile_snapshot_path(target)
    recovery = profile_restore_recovery_path(target)
    if not recovery.exists():
        return False

    try:
        current_bytes = target.read_bytes()
        previous_bytes = snapshot.read_bytes()
        recovery_bytes = recovery.read_bytes()
    except FileNotFoundError as error:
        raise RuntimeError(
            f"{recovery}: an interrupted profile restore needs manual review; "
            "no file was discarded"
        ) from error

    if current_bytes == recovery_bytes or previous_bytes == recovery_bytes:
        recovery.unlink()
        return True
    if current_bytes == previous_bytes:
        _require_expected_source(snapshot, previous_bytes)
        write_bytes_atomically(target, recovery_bytes)
        _require_expected_source(target, recovery_bytes)
        _require_expected_source(snapshot, previous_bytes)
        recovery.unlink()
        return True
    raise RuntimeError(
        f"{recovery}: an interrupted profile restore needs manual review; "
        "the current, previous, and recovery files were all preserved"
    )


def save_profile_text(
    path: str | Path,
    expected_bytes: bytes,
    proposed_bytes: bytes,
) -> Path:
    """Atomically snapshot and replace a profile that still matches its preview.

    The second comparison happens after the snapshot is durable.  If another
    editor's change is visible at either comparison, this operation stops.  If
    the final atomic write fails, the original file remains complete and the
    exact rollback snapshot is already available.  Callers must not run two
    profile writes against the same file simultaneously.
    """
    target = Path(path)
    snapshot = profile_snapshot_path(target)
    _require_no_restore_recovery(target)
    if proposed_bytes == expected_bytes:
        return snapshot

    try:
        proposed_text = proposed_bytes.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("proposed profile must be UTF-8 text") from error
    parse_config(proposed_text)

    _require_expected_source(target, expected_bytes)
    write_bytes_atomically(snapshot, expected_bytes)
    _require_expected_source(target, expected_bytes)
    write_bytes_atomically(target, proposed_bytes)
    return snapshot


def restore_profile_text(
    path: str | Path,
    expected_current_bytes: bytes,
    expected_snapshot_bytes: bytes,
) -> Path:
    """Safely exchange a previewed profile and its exact rollback snapshot.

    The former current bytes are first copied to an ignored recovery file.  A
    failed write is rolled back when possible; after a hard stop, both versions
    remain recoverable and ``recover_profile_restore`` resolves the known safe
    states before the next preview.
    """
    target = Path(path)
    snapshot = profile_snapshot_path(target)
    recovery = profile_restore_recovery_path(target)
    _require_no_restore_recovery(target)
    if expected_current_bytes == expected_snapshot_bytes:
        return snapshot

    try:
        proposed_text = expected_snapshot_bytes.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("profile snapshot must be UTF-8 text") from error
    parse_config(proposed_text)

    _require_expected_source(target, expected_current_bytes)
    _require_expected_source(snapshot, expected_snapshot_bytes)
    write_bytes_atomically(recovery, expected_current_bytes)
    try:
        _require_expected_source(target, expected_current_bytes)
        _require_expected_source(snapshot, expected_snapshot_bytes)
        write_bytes_atomically(target, expected_snapshot_bytes)
        _require_expected_source(snapshot, expected_snapshot_bytes)
        write_bytes_atomically(snapshot, expected_current_bytes)
    except BaseException:
        _roll_back_restore(
            target,
            snapshot,
            recovery,
            expected_current_bytes,
            expected_snapshot_bytes,
        )
        raise
    recovery.unlink()
    return snapshot


def _requested_edits(
    current: LogisticsConfig, edits: ProfileEdits
) -> tuple[dict[str, str | _Remove], LogisticsConfig]:
    defaults = LogisticsConfig()
    replacements = {}
    raw_requests = {}
    for key in EDITABLE_FIELDS:
        value = getattr(edits, key)
        if value is None:
            continue
        if key != "large_item_policy" and value is not REMOVE:
            if isinstance(value, bool) or not isinstance(value, Decimal):
                raise ValueError(f"logistics.{key} must be a decimal number")
        raw_requests[key] = value
        replacements[key] = getattr(defaults, key) if value is REMOVE else value

    proposed = replace(current, **replacements)
    requested: dict[str, str | _Remove] = {}
    for key, raw_value in raw_requests.items():
        if raw_value is REMOVE:
            requested[key] = REMOVE
            continue
        if getattr(proposed, key) != getattr(current, key):
            requested[key] = _toml_value(key, getattr(proposed, key))
    return requested, proposed


def _document_after_edits(
    document: dict,
    edits: dict[str, str | _Remove],
    proposed: LogisticsConfig,
) -> dict:
    expected = copy.deepcopy(document)
    logistics = expected.setdefault(LOGISTICS_TABLE, {})
    for key, edit in edits.items():
        if edit is REMOVE:
            logistics.pop(key, None)
            continue
        value = getattr(proposed, key)
        logistics[key] = value.value if isinstance(value, LargeItemPolicy) else value
    return expected


def _layout(lines: list[str]) -> _Layout:
    headers = [
        index
        for index, line in enumerate(lines)
        if _LOGISTICS_HEADER.fullmatch(_body(line))
    ]
    if len(headers) > 1:
        raise _unsupported()
    if not headers:
        return _Layout(None, len(lines), {})

    header = headers[0]
    end = next(
        (
            index
            for index in range(header + 1, len(lines))
            if _looks_like_table_header(_body(lines[index]))
        ),
        len(lines),
    )
    targets = {}
    for index in range(header + 1, end):
        for key in EDITABLE_FIELDS:
            target = _target_line(lines[index], index, key)
            if target is None:
                continue
            if key in targets:
                raise _unsupported()
            targets[key] = target
    return _Layout(header, end, targets)


def _target_line(line: str, index: int, key: str) -> _TargetLine | None:
    body, ending = _body_and_ending(line)
    matched = re.fullmatch(
        rf"(?P<prefix>[ \t]*{re.escape(key)}[ \t]*=[ \t]*)(?P<right>.*)",
        body,
    )
    if matched is None:
        return None

    right = matched.group("right")
    comment_at = right.find("#")
    before_comment = right if comment_at < 0 else right[:comment_at]
    value = before_comment.rstrip(" \t")
    suffix = before_comment[len(value) :]
    if comment_at >= 0:
        suffix += right[comment_at:]
    pattern = _POLICY_VALUE if key == "large_item_policy" else _NUMBER_VALUE
    if pattern.fullmatch(value) is None:
        raise _unsupported()
    return _TargetLine(index, matched.group("prefix"), suffix, ending)


def _require_canonical_targets(layout: _Layout, explicit: set[str]) -> None:
    for key in EDITABLE_FIELDS:
        if key in explicit and key not in layout.targets:
            raise _unsupported()


def _change_existing_lines(
    original: str,
    lines: list[str],
    changes: dict[int, str | None],
) -> str:
    if not changes:
        return original
    had_final_ending = _has_final_ending(original)
    changed = "".join(
        line if index not in changes else changes[index] or ""
        for index, line in enumerate(lines)
    )
    if not had_final_ending and _has_final_ending(changed):
        changed = _without_final_ending(changed)
    return changed


def _insert_missing(text: str, missing: dict[str, str]) -> str:
    lines = text.splitlines(keepends=True)
    layout = _layout(lines)
    bodies = [f"{key} = {missing[key]}" for key in EDITABLE_FIELDS if key in missing]
    if layout.header is None:
        bodies.insert(0, "[logistics]")
        if lines and _body(lines[-1]).strip():
            bodies.insert(0, "")
        return _insert_at_end(text, lines, bodies, _preferred_newline(lines, None))

    candidate = text
    for position, key in enumerate(EDITABLE_FIELDS):
        if key not in missing:
            continue
        lines = candidate.splitlines(keepends=True)
        layout = _layout(lines)
        preceding = [
            layout.targets[previous].index
            for previous in EDITABLE_FIELDS[:position]
            if previous in layout.targets
        ]
        insertion = max(preceding) + 1 if preceding else layout.header + 1
        newline = _preferred_newline(lines, layout.header)
        candidate = _insert_at(
            candidate,
            lines,
            insertion,
            [f"{key} = {missing[key]}"],
            newline,
        )
    return candidate


def _insert_at(
    original: str,
    lines: list[str],
    index: int,
    bodies: list[str],
    newline: str,
) -> str:
    if index == len(lines):
        return _insert_at_end(original, lines, bodies, newline)
    inserted = "".join(f"{body}{newline}" for body in bodies)
    return "".join((*lines[:index], inserted, *lines[index:]))


def _insert_at_end(
    original: str,
    lines: list[str],
    bodies: list[str],
    newline: str,
) -> str:
    had_final_ending = _has_final_ending(original)
    prefix = "".join(lines)
    if prefix and not had_final_ending:
        prefix += newline
    inserted = newline.join(bodies)
    if had_final_ending:
        inserted += newline
    return prefix + inserted


def _preferred_newline(lines: list[str], header: int | None) -> str:
    if header is not None:
        ending = _body_and_ending(lines[header])[1]
        if ending:
            return ending
    endings = [_body_and_ending(line)[1] for line in lines]
    endings = [ending for ending in endings if ending]
    if not endings:
        return "\n"
    return max(dict.fromkeys(endings), key=endings.count)


def _toml_value(key: str, value: LargeItemPolicy | Decimal) -> str:
    if key == "large_item_policy":
        return f'"{value.value}"'
    return _plain_decimal(value)


def _plain_decimal(value: Decimal) -> str:
    if not value:
        return "0"
    written = format(value, "f")
    return written.rstrip("0").rstrip(".") if "." in written else written


def _comment_after_removal(target: _TargetLine) -> str | None:
    comment_at = target.suffix.find("#")
    if comment_at < 0:
        return None
    indentation = target.prefix[: len(target.prefix) - len(target.prefix.lstrip(" \t"))]
    return f"{indentation}{target.suffix[comment_at:]}{target.ending}"


def _require_expected_source(path: Path, expected: bytes) -> None:
    try:
        current = path.read_bytes()
    except FileNotFoundError as error:
        raise RuntimeError(f"{path} changed after the profile preview; run it again") from error
    if current != expected:
        raise RuntimeError(f"{path} changed after the profile preview; run it again")


def _require_toml_path(path: Path) -> None:
    if path.suffix != ".toml":
        raise ValueError(
            f"{path}: profile editing requires a .toml configuration path "
            "so backups and interrupted-write files remain ignored by Git"
        )


def _require_no_restore_recovery(path: Path) -> None:
    recovery = profile_restore_recovery_path(path)
    if recovery.exists():
        raise RuntimeError(
            f"{recovery}: recover the interrupted profile restore before writing"
        )


def _roll_back_restore(
    target: Path,
    snapshot: Path,
    recovery: Path,
    current_bytes: bytes,
    previous_bytes: bytes,
) -> None:
    """Best-effort rollback while retaining a unique recovery copy on failure."""
    try:
        if target.read_bytes() == previous_bytes and snapshot.read_bytes() == previous_bytes:
            write_bytes_atomically(target, current_bytes)
    except (OSError, RuntimeError):
        pass

    try:
        recovery_bytes = recovery.read_bytes()
        if target.read_bytes() == recovery_bytes or snapshot.read_bytes() == recovery_bytes:
            recovery.unlink()
    except OSError:
        pass


def _looks_like_table_header(body: str) -> bool:
    stripped = body.lstrip(" \t")
    return stripped.startswith("[") and "]" in stripped


def _body(line: str) -> str:
    return _body_and_ending(line)[0]


def _body_and_ending(line: str) -> tuple[str, str]:
    if line.endswith("\r\n"):
        return line[:-2], "\r\n"
    if line.endswith(("\n", "\r")):
        return line[:-1], line[-1]
    return line, ""


def _has_final_ending(text: str) -> bool:
    return text.endswith(("\r\n", "\n", "\r"))


def _without_final_ending(text: str) -> str:
    return _body_and_ending(text)[0]


def _unsupported() -> ValueError:
    return ValueError(
        "profile editor cannot safely update this [logistics] representation; "
        "use ordinary one-line keys and values"
    )
