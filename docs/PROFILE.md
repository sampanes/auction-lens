# Profile editing

`config/local.toml` remains Auction Lens's one source of truth. The profile
commands make a small part of it easier to read and change; they do not create
another preference file or rules engine.

Writable profile paths must end in `.toml`. That narrow rule ensures their
rollback and interrupted-write files match the repository's Git ignore rules,
including when `--config` names a custom file.

## Read it

```cmd
.venv\Scripts\auction-lens.exe profile
```

This translates the effective interests, condition rules, locations, handling
limits, report shape, and valuation switch into plain language. It reads no
credentials, listings, history, delivery receipts, or network resource, and it
writes nothing.

## Edit large-item handling

```cmd
.venv\Scripts\auction-lens.exe profile --edit
```

The first focused questionnaire asks:

1. Should a large lot remain visible with a handling question, remain visible
   without one, or be rejected?
2. Above what published weight does extra planning begin?
3. Above what published dimension does extra planning begin?

These are stable actions the software can take. Truck ownership, a friend's
availability, and today's route are deliberately not stored as a personal
ontology. Seller loading assistance and the `logistics` command settle unusual
lots individually; `daily --visiting BRANCH` handles today's route without
changing tomorrow's profile.

Pressing Enter keeps the effective answer. If that answer came from a default,
the editor leaves the key absent instead of restating the default in TOML.
Typing `default` removes an existing assignment and returns that one answer to
the built-in default.

## Preview before writing

The editor first renders the complete proposed profile, then a zero-context
unified diff containing only the TOML lines that would change. If line-ending
bytes or the final newline differ, the preview also names the exact line ranges
using CRLF, LF, CR, or no terminator. Unrelated provider and adapter settings
are not copied into the preview. Only `y` or `yes` writes. Any other answer,
end-of-input, interruption, or noninteractive terminal leaves the configuration
untouched.

The changed document is parsed by the same loader as a daily run before it is
shown. Comments, ordering, unrelated bytes, existing line endings, and an
absent final newline are preserved. The source is checked again immediately
before each atomic replacement, so a change already visible then is refused.
Do not run two profile editors against the same file simultaneously.

## Roll back

Before applying a confirmed edit, Auction Lens atomically saves the exact old
bytes as:

```text
config/local.toml.previous
```

That snapshot and any interrupted-write temporary files are ignored by Git.
Preview and restore it with:

```cmd
.venv\Scripts\auction-lens.exe profile --restore
```

Restoring uses the same validation, diff, and confirmation. During the swap it
keeps the former current bytes in a third ignored recovery file until both
atomic writes are durable. A normal write failure rolls back; a hard stop is
reconciled before the next profile edit or restore. The file being replaced
becomes the new `.previous` snapshot, so an accidental restore can be reversed
by restoring once more.

## Deliberate boundary

Interests, condition profiles, valuation sources, authorization, email, and
webhooks remain hand-edited in this first version. They either have richer
structure or carry operational/security consequences that three friendly
prompts would hide. A future questionnaire should extend the same editor only
when a repeated real task makes the added surface worthwhile.
