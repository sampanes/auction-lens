# Conventions

`ARCHITECTURE.md` says where code lives. This says what it should look like when
it gets there, so that a change made a year from now is indistinguishable from
one made today. If you are about to invent a new way of doing something already
done here, do it the existing way instead, or change every instance at once.

The goal is not short code. It is code where a reader who has never seen the
project can answer their question without opening a second file.

## 1. A rule is written down once

Every value has exactly one place that decides whether it is acceptable.

| The rule | Where it goes | Example |
|---|---|---|
| What a value must be | the record's `__post_init__` | `port must be between 1 and 65535` |
| What kind of thing the file holds | `config/toml_reader.py` | `scoring.minimum_report_score must be a whole number` |
| Which table an operator must edit | `config/toml_reader.py`, via `in_section` | prefixes `reports.email: ` |

So a config builder does nothing but map keys to fields. If you find yourself
writing `if` after constructing a record, the check belongs in the record.

The payoff is that consumers never re-check. `send_email` does not verify that
`security` is a real mode, and `assess_logistics` does not verify that
`large_item_policy` is a real policy, because those records cannot hold anything
else. Before this rule existed the same four guards were written twice each.

The one exception: a table of names an operator chose, such as
`[conditions.penalties]`. No record can name a key it has never heard of, so the
reader checks those and names them precisely.

A valuation source's `settings` table is the same shape of problem: its keys
belong to whichever adapter the source named, so `config/schema.py` cannot list
them. `valuation/settings.py` gives that table the same two pieces anyway -- a
`Section` labelled `valuation.sources.<id>`, and a `RequestLimits` record -- so
an adapter reads its settings exactly the way the loader reads everything else.

## 2. A closed set of words is an enum

If a setting or a status may only be one of a few words, it is a `StrEnum`
(`RunMode`, `EmailSecurity`, `LargeItemPolicy`, `AcquisitionMode`,
`LogisticsStatus`, `CandidateCategory`). Never a bare `str` with a `frozenset`
of allowed values beside it.

- **Settle it at the edge.** The record turns the written word into the member
  (`_settle` in `config/schema.py`, `_decidable` in `models.py`), so text from
  TOML, SQLite, or argparse all arrive inside as the member.
- **Compare with `==`, never `is`.** `status is LogisticsStatus.INFEASIBLE` is
  silently `False` when `status` is the plain string `"infeasible"`, and that
  failure looks exactly like a listing that passed the gate. `==` is right in
  both cases, so there is nothing to remember.

## 3. `fields.py` is the vocabulary

Everything that reads a value uses its words, so an operator sees the same
sentence for the same mistake whatever file it was in.

- `require_*` checks a value that is already the right type, and returns it.
- `parse_*` coerces a loosely typed value from a listing file, applying the
  requirements on the way.

Listing input is coerced because CSV has no types. TOML input is **not** coerced,
because it does: a number written as `"70"` in a config file is a mistake worth
reporting, not something to quietly convert.

Every message names the field, because the person reading it has to go and edit
that field.

## 4. Parse at the boundary; trust the inside

Untrusted values are turned into strict records once, at the edge
(`ingest`, `config/loader`, the valuation adapters). After that, code uses them.

A record built from another record is *derived*, and does not re-check.
`ValuationObservation` enforces `low <= typical <= high` because it is built from
a file; `ValuationBand` does not, because it is computed from observations that
already passed. Re-checking derived data turns an arithmetic quirk into a crash.

## 5. Splitting a module

Split when two parts change for different reasons. Never split to make a file
shorter. A module with a large interface and a small implementation costs a
reader more than the long file it replaced -- they now have to learn a name, an
import, and a call to get to two lines of logic.

Concretely, a new module earns its place if you can finish this sentence without
using "and": *this module answers the question ...*.

## 6. Deliberate repetition

Some repetition is cheaper than the abstraction that would remove it. Where that
is true, it is written down here so nobody "fixes" it badly.

- **`Listing.from_mapping` names every field by hand**, and
  `REQUIRED_LISTING_FIELDS` lists five of them again. A table-driven mapper would
  remove the repetition and replace it with a small framework a reader has to
  learn first. The explicit version *is* the documentation of the file format an
  operator hands us. Adding a field means editing the dataclass and that call.
- **SQL column lists appear in the schema and in the statements.** SQLite is the
  authority on its own tables; an ORM to avoid retyping them would be a much
  larger thing to understand than the two lists.

## 7. Reports: what, then how

`reporting/findings.py` decides what a report says. `text.py` and `html.py`
decide only what that looks like. A renderer must not reach into a `Candidate`,
and `findings.py` must not know about terminals, markup, or escaping.

This is enforced by a test (`BothRenderingsSayTheSameThingTests`) which asserts
that every fact and every open question reaches both renderings. Adding a new
format means writing one function against `Report`.

## 8. Names over comments

A comment says *why*; the code says *what*. A number that encodes a judgement is
a named constant (`ENDING_SOON_BONUS`, `SAMPLE_SIZE_CAP`, `FACTS_PER_LINE`), not
a literal inside an expression.

Every module opens with a docstring saying what question it answers. This is
checked (`D100`).

## 9. ASCII only

No emoji, box drawing, arrows, em-dashes, smart quotes, or Greek letters in any
tracked file. Use `[OK]`, `[X]`, `->`, `--`. If a specific codepoint is genuinely
needed, escape it (`"\u00d7"`, as `fields.py` does). Checked by `scripts/check-ascii.py`.

## 10. Tests

`tests/` mirrors the module layout, one file per area, with fixtures and fakes in
`tests/support.py`. Nothing touches the network, SMTP, or a real provider.

Name a test for the behavior it protects, not the function it calls:
`test_a_misspelled_acquisition_mode_is_refused_at_load_time`, not
`test_load_config_4`. When a test exists because of a specific past mistake, say
so in its docstring.

## Adding something

- **A setting**: add the field to the record in `config/schema.py`, its rule to
  that record's `__post_init__`, and one line to the matching builder in
  `config/loader.py`. Then add it to `config/providers/nellis.example.toml`.
- **A scoring signal**: a named constant and a function in `scoring/signals.py`.
  Put anything both interests and anomaly discovery need on `ScoringContext`.
- **A valuation source**: prefer configuration. If it genuinely needs code,
  implement `collect(listing)` and register it in `valuation/registry.py`.
- **Something to say in reports**: add it to the view model in
  `reporting/findings.py` first. Both renderings then have to show it.
- **A command**: parser in `cli/parser.py`, function in `cli/commands.py`,
  mapping in `cli/__init__.py`. Real logic belongs in `pipeline` instead.

## Running the checks

`scripts/check.py` owns the check list used by CI. On Windows,
`scripts\test.cmd` runs that driver with the project virtual environment:

```
compileall  ->  check-ascii  ->  check-imports  ->  ruff  ->  unittest
```
