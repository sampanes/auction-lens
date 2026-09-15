# Conventions

`ARCHITECTURE.md` says where code lives. This says what it should look like when
it gets there. The goal is not short code. It is code where a reader can answer
their question without learning a private framework first.

## 1. Write a rule down once

Every value has one place that decides whether it is acceptable.

| The rule | Where it goes | Example |
|---|---|---|
| What an already-typed value must be | its record's `__post_init__` | `port must be between 1 and 65535` |
| What kind of value TOML contains | `config/toml.py` | `scoring.minimum_report_score must be a whole number` |
| Which table an operator must edit | `config/toml.py`, through `in_section` | prefixes `reports.email: ` |

A config builder maps keys to record fields. If it checks a value after the
record is constructed, that check probably belongs to the record.

Open-ended adapter settings are the exception. Their valid keys depend on the
adapter named in TOML, so `pricing/sources.py` supplies a labelled `Section` and
shared request limits. Each adapter validates only the keys it understands.

`listings/conditions.py` applies the same rule to provider vocabulary. An
answer such as `Yes` can be good on one condition axis and bad on another; its
polarity is recorded once rather than guessed at every call site.

## 2. Use an enum for a closed set of words

If a setting or status may only be one of a few words, use a `StrEnum`, not a
bare string plus a separate collection of allowed values.

- Settle text at the edge. TOML, SQLite, and command-line strings become enum
  members while their containing record is built.
- Compare members with `==`, never `is`. Equality remains correct even when an
  older persisted value first arrives as its equivalent string.

Examples include `RunMode`, `EmailSecurity`, `LargeItemPolicy`,
`AcquisitionMode`, `LogisticsStatus`, and `CandidateCategory`.

## 3. Use `values.py` for shared value vocabulary

Everything that parses or validates the same kind of scalar should use the same
words, so an operator sees the same error wherever the bad value entered.

- `require_*` checks a value already of the right type and returns it.
- `parse_*` turns loose listing or command input into a typed value while
  applying the requirements.

Listing input is coerced because CSV has no types. TOML input is not coerced
because TOML does: writing `"70"` where a number belongs is an actionable
configuration mistake.

Every validation error names the field the person must edit.

## 4. Parse at the boundary; trust the inside

Untrusted values become strict records once, at the edge: `listings/files.py`,
`config/load.py`, provider parsers, and pricing adapters. Code inside the
application uses those records rather than repeatedly checking their fields.

A record derived entirely from validated records does not need to repeat their
input checks. Revalidating computed data can turn an arithmetic quirk into an
unrelated crash.

## 5. Split by reason to change

A module earns its place when this sentence has one answer: *this module
answers the question ...*.

Do not split merely to lower a line count. A tiny wrapper with a broad name
costs more than the two obvious lines it hides. Conversely, do split unrelated
parsing, persistence, rendering, and policy even when they once fitted in one
file: they change for different reasons and appear as different concepts in the
directory tree.

Avoid names such as `base`, `engine`, `manager`, `service`, and `utils` when a
domain noun or verb says what the file actually owns.

## 6. Prefer explicit code over private machinery

Some repetition is cheaper than an abstraction:

- `Listing.from_mapping` names each field by hand. That explicit call is also
  the documentation for the canonical input format.
- SQL column lists appear in the schema and statements. An ORM would be much
  more to understand than those nearby lists.
- Package `__init__.py` files are markers. Import a name from its owning module
  so a code search and the directory tree give the same answer.

## 7. Reports decide what, then how

`reports/findings.py` decides what a report says and writes those decisions into
format-neutral records from `reports/records.py`. `reports/text.py` and
`reports/html.py` decide only what those facts look like. Renderers do not reach
back into a `Candidate`, and report construction does not know about escaping or
terminal presentation.

The cross-channel contract tests require text, HTML, SMTP, and webhook output to
retain the same important facts. A new output format should be one renderer
against the report records, not another analysis workflow.

## 8. Names say what; comments say why

A reader should be able to follow the ordinary path from function and variable
names alone. A comment earns its space by explaining a constraint, tradeoff, or
failure mode that the code cannot say.

Judgement numbers are named constants (`ENDING_SOON_BONUS`,
`SAMPLE_SIZE_CAP`, `FACTS_PER_LINE`), not unexplained literals in expressions.
Every module begins with a docstring stating its one question; Ruff checks this.

## 9. Keep tracked text safe and portable

Tracked files use ASCII: no smart quotes, emoji, box drawing, or accidental
mojibake. Escape a genuinely required codepoint. `scripts/check-ascii.py`
enforces the rule.

Private runtime files never enter Git. This includes `.env`, `config/local.toml`,
provider caches, delivery ledgers, databases, watchlists, and profile backups.

## 10. Test behavior, boundaries, and old data

Name a test for the behavior it protects, not the function it happens to call:
`test_a_misspelled_acquisition_mode_is_refused_at_load_time`, not
`test_load_config_4`. When a particular past mistake explains a test, put that
reason in its docstring.

- `tests/contracts/` pins public command and report meaning while internals move.
- `fixtures/compatibility/` proves old local data still opens.
- Provider tests use redacted transcripts and fake openers, never live traffic.
- Shared synthetic examples live in `tests/support.py`.

## Adding something

- **A setting:** add its field and invariant in `config/schema.py`, map it in
  `config/load.py`, document it in the public example, and test the exact key.
- **A listing fact:** add it to `listings/model.py` and map it explicitly at each
  provider/canonical boundary.
- **A match rule or score:** put shared admission policy in
  `matching/evaluate.py`; interest-specific evidence belongs in
  `matching/interests.py`. Keep explanatory score constants by the matching
  record that uses them.
- **A price source:** prefer a new TOML `[[valuation.sources]]` entry. For a new
  input mechanism, implement the `ValuationAdapter` contract in `pricing/` and
  register it in `pricing/value.py`.
- **Something to say in reports:** add the fact to `reports/records.py`, populate
  it in `reports/findings.py`, and render it in every supported format.
- **A provider:** keep its page discovery and parser in `providers/<name>/` and
  emit provider-neutral `Listing` records. Do not let provider details leak into
  matching.
- **A command:** describe flags in `cli/parser.py`, put behavior in the plainly
  named feature or workflow module, and add one dispatch entry in
  `cli/__init__.py`.

## Running the checks

`scripts/check.py` owns the list used locally and in CI. On Windows,
`scripts\test.cmd` runs it with the project virtual environment:

```
compileall  ->  check-ascii  ->  check-imports  ->  ruff  ->  unittest
```
