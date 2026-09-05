# Roadmap

## Profile wizard and scenario planner

Status: planned, after reliable provider normalization.

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
