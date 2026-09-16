# Feedback-assisted tuning

Feedback records what one recommendation taught you. It gives a future review
evidence to work from without turning one reaction into a hidden preference
change.

Every finding already prints a `Watch key`. Record a quick reaction with that
same key:

```cmd
.venv\Scripts\auction-lens.exe feedback yes --key nellis/synthetic-001
.venv\Scripts\auction-lens.exe feedback wrong-item --key nellis/synthetic-002 ^
  --interest soundbar ^
  --note "stand only; no keyboard included"
```

The first command is enough when the lot matched only one interest. Use
`--interest` to identify the configured interest by id or name when a lot
matched more than one. `--source` plus `--listing-id` is the longer equivalent
of `--key`.

## Labels

| Label | What it says |
|---|---|
| `yes` | This was a useful recommendation. |
| `maybe` | It may be useful, but the evidence is not decisive yet. |
| `no` | It was not useful; no narrower reason is being claimed. |
| `wrong-item` | The words matched, but the listing was an accessory, substitute, or otherwise not the requested item. |
| `too-expensive` | The item was relevant, but the price policy admitted a poor value. |
| `logistics-impossible` | The item and price may be relevant, but this particular recommendation could not be collected or transported. |
| `clear` | Stop treating the current reaction as effective while retaining its audit trail. |
| `review` | Read effective feedback and look for repeated, proposal-worthy evidence. |

These labels answer a different question from the watchlist and logistics
commands:

- `feedback` asks whether a recommendation was useful and why.
- `watch --verdict ...` records whether you are watching, hunting, passing on,
  or have won a lot. It also owns explicit purchase fulfillments.
- `logistics` records the practical handling answer for one listing. Feedback
  labelled `logistics-impossible` is evidence about recommendation quality; it
  does not replace that per-listing decision.

One command may therefore be appropriate in more than one feature. Passing on
a listing and explaining that it was the wrong item are related facts, not two
spellings of the same fact.

## Corrections do not erase history

Feedback is an append-only event log at `private/feedback.json`. The file is
ignored by Git and belongs to one operator, just like the watchlist. Recording
another label for the same Watch key and interest corrects the effective
answer; the earlier event remains available for audit. `clear` appends the
explicit correction that no reaction is currently effective. It does not
delete either event.

This means an accidental `no` is safe to correct:

```cmd
.venv\Scripts\auction-lens.exe feedback yes --key nellis/synthetic-001
```

Or clear it without substituting another opinion:

```cmd
.venv\Scripts\auction-lens.exe feedback clear --key nellis/synthetic-001
```

`--note` is optional context for the human reviewing the evidence. Do not put
credentials or anything you would not want in a local plaintext file there.

## Review first, edit never

Review requires repeated evidence from distinct items. The default threshold is
three, so repeated runs, duplicate events, and several corrections to one lot
cannot manufacture a pattern:

```cmd
.venv\Scripts\auction-lens.exe feedback review
```

Use `--minimum-evidence NUMBER` to explore a different threshold. Lowering it
changes only this review; it does not weaken a saved preference or make a
proposal apply itself.

Proposals are intentionally conservative and limited to patterns the program
can explain as a small, reviewable configuration change. A repeated label is
not automatically a rule, and unsupported patterns remain evidence rather than
being dressed up as an edit. Most importantly, feedback never rewrites
`config/local.toml`.

The first implemented proposal is a narrower price ceiling. It needs both an
accepted item (`yes` or `maybe`) and an item marked `too-expensive`, enough
distinct items to meet the threshold, evidence recorded against one
configuration version, and a clean numerical gap with no contradictory example.
Only then can review suggest lowering `max_total_cost` or
`maximum_retail_ratio` to the highest accepted example. `wrong-item`, `no`, and
`logistics-impossible` can form visible patterns, but they do not yet pretend to
know which words or physical constraint a human would choose for a TOML edit.

After reading the review in the terminal, save each available proposal as an
immutable private artifact:

```cmd
.venv\Scripts\auction-lens.exe feedback review --save
```

Saved artifacts go under `private/proposals/` by default and are also ignored
by Git. Saving is documentation, not approval: inspect the proposed field
changes, edit the real configuration yourself if they are right, then run
`profile` or `doctor` to read the effective result back.

An artifact contains the target, before/after values, configuration digest, and
feedback event ids needed to reproduce the reasoning. It deliberately omits
listing titles, notes, links, contact details, and local paths.

## Alternate private files

The defaults keep the command short:

| Purpose | Default |
|---|---|
| Lot context | `private/watchlist.json` |
| Append-only events | `private/feedback.json` |
| Configuration snapshot and review | `config/local.toml` |
| Saved proposal artifacts | `private/proposals/` |

Use `--watchlist`, `--feedback-file`, `--config`, or `--proposal-dir` when an
operator deliberately keeps separate profiles. All such paths should remain
private and ignored.
