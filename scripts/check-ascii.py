"""Fail if any committable text file contains unsafe console characters.

Non-ASCII in source, log lines, or committed configuration has repeatedly broken
this project on Windows consoles. Escape the character (\\u00d7) if a specific
codepoint is genuinely needed.

A bare control byte is the same bug wearing ASCII, and it hides better. A
vertical tab written where a path separator was meant leaves a command that
reads as an ordinary typo, so the eye corrects it and the file stays broken.

Usage: python scripts/check-ascii.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

CHECKED_SUFFIXES = {
    ".py",
    ".toml",
    ".md",
    ".cmd",
    ".ps1",
    ".yml",
    ".yaml",
    ".json",
    ".xml",
    ".html",
    ".cfg",
    ".txt",
    ".js",
    ".example",
}


def committable_files() -> list[Path]:
    """Everything a commit could carry, which includes what is not added yet.

    Reading only the index clears a file on the one run where it matters: a new
    file goes unexamined until it is tracked, and it becomes tracked by being
    committed. --others adds the untracked ones; --exclude-standard keeps the
    ignored ones out, which is also what stops this gate printing a line of
    private configuration to the console.
    """
    listing = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return sorted({Path(line) for line in listing.splitlines() if line})


def offending_lines(path: Path) -> list[tuple[int, str]]:
    found = []
    for number, line in enumerate(path.read_bytes().split(b"\n"), start=1):
        # Tabs and CRLF are ordinary text. Other control bytes can ring a bell,
        # move a cursor, or hide what a command really contains.
        if any(byte > 127 or (byte < 32 and byte not in {9, 13}) for byte in line):
            readable = (
                line.decode("utf-8", errors="replace")
                .strip()
                .encode("unicode_escape")
                .decode("ascii")
            )
            found.append((number, readable))
    return found


def main() -> int:
    failures = 0
    for path in committable_files():
        if path.suffix.lower() not in CHECKED_SUFFIXES or not path.is_file():
            continue
        for number, line in offending_lines(path):
            print(f"{path}:{number}: unsafe text character: {line}")
            failures += 1
    if failures:
        print(f"[X] {failures} line(s) contain unsafe text characters")
        return 1
    print("[OK] all committable text files are safe ASCII")
    return 0


if __name__ == "__main__":
    sys.exit(main())
