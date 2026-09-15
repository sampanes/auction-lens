"""One-step profile backups that remain safe when a write is interrupted.

The guided editor previews exact bytes before it writes. These functions keep
that promise at the disk boundary: they refuse a profile changed since the
preview, preserve the previous bytes beside it, and recover known-safe states
after an interrupted restore.
"""

from __future__ import annotations

from pathlib import Path

from ..files import write_bytes_atomically
from .load import parse_config


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


def save_profile_text(
    path: str | Path,
    expected_bytes: bytes,
    proposed_bytes: bytes,
) -> Path:
    """Atomically snapshot and replace a profile that still matches its preview.

    The second comparison happens after the snapshot is durable. If another
    editor's change is visible at either comparison, this operation stops. If
    the final atomic write fails, the original file remains complete and the
    exact rollback snapshot is already available. Callers must not run two
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

    The former current bytes are first copied to an ignored recovery file. A
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


def recover_profile_restore(path: str | Path) -> bool:
    """Finish cleaning up, or roll back, an interrupted snapshot swap.

    A restore keeps the former current file in a third, ignored location until
    both halves of the swap are durable. Most failures are rolled back inside
    the original call. This function handles the smaller hard-stop window in
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


def _require_expected_source(path: Path, expected: bytes) -> None:
    try:
        current = path.read_bytes()
    except FileNotFoundError as error:
        raise RuntimeError(
            f"{path} changed after the profile preview; run it again"
        ) from error
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
