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
