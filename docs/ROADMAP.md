# Roadmap

## Profile wizard and scenario planner

Status: read-only profile explanation implemented; guided editing remains
planned after reliable provider normalization.

`auction-lens profile` is the first, deliberately read-only slice. It renders
the effective configuration in plain language without reading credentials or
runtime data. A future editor should reuse that explanation as its confirmation
screen rather than inventing a second representation of the rules.

The underlying TOML remains the source of truth, but routine configuration should
not require hand-editing it. A future guided editor should:

1. Ask only questions relevant to current listings or an explicit setup task.
2. Distinguish stable preferences from temporary circumstances.
3. Present a plain-language summary and exact configuration diff before writing.
4. Require confirmation, write atomically, and retain a rollback snapshot.
5. Convert repeated per-listing decisions into a proposed general rule only after
   asking the operator.

The wizard must remain an editor for the existing profile, not a second rules
engine. Personal answers stay in ignored local configuration.

## Feedback-assisted tuning

Reports should eventually accept compact feedback such as `yes`, `maybe`, `no`,
`wrong model`, `too expensive`, and `logistics impossible`. Feedback remains an
observation until a repeated pattern supports a proposed, reviewable config
change. Auction Lens should never silently rewrite preferences.

## Interests that retire themselves

Status: not started. Less of it is missing than it first appears.

Some wants are for exactly one thing. There is no second metal shed, no second
two-person kayak, and no reason to keep reading about them after one is won.
Today every interest reports forever, so a satisfied want becomes noise that
the operator has to remember to delete by hand.

An interest should be able to say how many it wants:

```toml
[[interests]]
name = "metal shed"
wanted = 1
```

and stop matching once that many have been won. Most of the storage already
exists: the watchlist carries a verdict per lot and `Verdict.WON` is one of
them, so "this one was bought" is already recordable and already sortable.

The gap is narrower than that. The watchlist stores lots, not the interest that
matched them, so nothing today can answer "has a metal shed been won" -- only
"has lot 127449824 been won". Closing that means either recording the matching
rule alongside the verdict, or re-matching a won lot against the rules when the
count is needed. The first is a storage change and a migration; the second is
free but re-reads the rules to answer a question about the past, which is the
kind of thing that quietly disagrees with itself after a rule is edited.

That choice is the work. The matching change itself is one gate.

The rule must stay a preference rather than a purchase record. An interest that
retires itself should be easy to un-retire, and the configuration should still
read as a description of what the operator wants, not as inventory.

## Open questions

These are decisions, not features. They are written down so they stop being
rediscovered.

- **Should `AGENTS.md` be tracked?** It is in `.gitignore` today, which means a
  clone carries no instructions for the coding agents that work on it, and each
  session reconstructs them. Tracking it would make those conventions
  reviewable in the same place as the code they govern. The argument against is
  that it is a personal working file rather than part of the project.
