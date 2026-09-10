# Roadmap

## Profile wizard and scenario planner

Status: readback and the focused large-item handling editor are implemented;
broader editing remains planned only where repeated use justifies it.

`auction-lens profile` is the deliberately read-only view. It renders
the effective configuration in plain language without reading credentials or
runtime data. `profile --edit` reuses that exact explanation as its confirmation
screen, shows a zero-context TOML diff, requires confirmation, writes atomically,
and retains an ignored rollback snapshot. `profile --restore` previews the same
way before exchanging the current file with that snapshot.

The underlying TOML remains the source of truth. The first questionnaire edits
only the three stable decisions behind large-item handling. Any extension should:

1. Ask only questions relevant to current listings or an explicit setup task.
2. Distinguish stable preferences from temporary circumstances.
3. Present a plain-language summary and exact configuration diff before writing.
4. Require confirmation, write atomically, and retain a rollback snapshot.
5. Convert repeated per-listing decisions into a proposed general rule only after
   asking the operator.

The wizard remains an editor for the existing profile, not a second rules
engine. It records what action to take, not a biography about trucks, trailers,
or available friends. Personal answers stay in ignored local configuration.

## Feedback-assisted tuning

Status: reliable match provenance and explicit outcome allocation implemented;
compact yes/maybe/no feedback and rule proposals remain planned.

Reports should eventually accept compact feedback such as `yes`, `maybe`, `no`,
`wrong model`, `too expensive`, and `logistics impossible`. Feedback remains an
observation until a repeated pattern supports a proposed, reviewable config
change. Auction Lens should never silently rewrite preferences.

## Interests that retire themselves

Status: implemented.

Some wants are for exactly one thing. There is no second metal shed, no second
two-person kayak, and no reason to keep reading about them after one is won.
An interest can say how many wins satisfy it:

```toml
[[interests]]
name = "metal shed"
id = "yard-shed"
wanted = 1
```

The watchlist stores stable, readable references to every interest that surfaced
a followed lot. A `won` verdict alone retires nothing: the operator explicitly
assigns which matching interests that purchase fulfilled. Report progress calls
these confirmed fulfillments, not wins. This avoids the
dangerous shortcut where one multi-match lot satisfies every want.

Retirement is derived, never stored. The TOML remains a preference and the
watchlist remains purchase history. Correcting the verdict, clearing its
allocations, or increasing `wanted` reopens the rule without config surgery.
Clearing an allocation also records the legitimate reviewed-none outcome, so a
reopened rule does not come with a misleading reminder about that old purchase.

Legacy watchlists remain readable, but old wins retire nothing rather than being
guessed into a current interest. Finite rules require a stable id; match and
fulfillment references keep that id plus the display name seen at the time, so
a renamed rule remains continuous.

## Delivery deduplication

Status: implemented.

Observation history and delivery history answer different questions. The
observation database knows what the collector saw; the private delivery ledger
knows which revision a particular destination accepted. Unchanged listings are
removed before each destination's report cap, while a changed bid or a new
auction id remains eligible. Email, webhook, findings, and watchlist selections
keep independent receipt streams, and `--repeat-delivery` provides an explicit
override.

Receipts are committed only after a transport returns successfully. Overlapping
runs serialize around that decision, while a failed channel remains eligible
for retry. A remote can accept a report immediately before the connection,
process, or local commit fails, so the guarantee is deliberately at-least-once,
not exactly-once.
See [Delivery receipts](DELIVERY.md) for the operator contract.

## Open questions

These are decisions, not features. They are written down so they stop being
rediscovered.

- **Should `AGENTS.md` be tracked?** It is in `.gitignore` today, which means a
  clone carries no instructions for the coding agents that work on it, and each
  session reconstructs them. Tracking it would make those conventions
  reviewable in the same place as the code they govern. The argument against is
  that it is a personal working file rather than part of the project.
