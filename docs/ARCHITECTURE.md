# Architecture

The source tree uses auction features for directories and plain verbs for the
workflows at the package root. A normal run can be read in one direction:

```text
daily
  -> collect -> providers -> listings
  -> matching -> pricing
  -> history + watchlist
  -> reports -> email or webhook
```

Configuration supplies policy to every stage. Providers own the authorized
boundary to an auction site. The command line names the public doors into the
features, but it does not define a second version of their rules.

For a file-level answer to a concrete question, use the
[question-first code map](CODE_MAP.md).

## Top-level workflows

The files at `src/auction_lens/` are the shortest path through the application:

| Workflow | Owns |
|---|---|
| `setup.py` | Creating the ignored configuration and settings a clone cannot carry |
| `doctor.py` | Checking an unattended run locally, without network or state changes |
| `collect.py` | Fetching, discovering, and reparsing authorized provider pages |
| `daily.py` | Running the full daily flow or analysing an existing listings file |

`daily.py` is the first file to open for the complete operator path. It reads
top to bottom as configuration, discovery, analysis, report construction, and
optional delivery.

## Feature directories

| Directory | Owns |
|---|---|
| `config/` | Feature-owned records for provider, interests, pricing, reports, logistics, and the assembled app; TOML loading and profile editing |
| `providers/` | Authorized HTTP behavior plus provider-specific discovery and parsing |
| `listings/` | The canonical provider-neutral listing, file formats, and condition evidence |
| `matching/` | Admission gates, interests, optional judging, logistics, ranking, and finite progress |
| `pricing/` | Configurable value sources, evidence provenance, and aggregation |
| `history/` | SQLite observations, closing-price evidence, and saved handling decisions |
| `watchlist/` | Human verdicts, price trails, fulfillment decisions, storage, and rendering |
| `reports/` | Shared report facts, rendering, destination filtering, transports, and receipts |
| `cli/` | The public parser, command dispatch, exit codes, and small interactive adapters |

The `reports/` split is deliberate. `records.py` defines what every channel can
receive, `findings.py` decides what to say, and `text.py` and `html.py` decide
how it looks. `email.py` and `webhook.py` own their transports. `delivery.py`
plans what one destination has not received, `receipts.py` remembers successful
revisions, and `send.py` keeps those operations in the safe order.

The `cli/` package is deliberately thin. `parser.py` is the only authority for
commands, flags, help, and defaults. Its dispatch map points each command at a
feature or top-level workflow; domain decisions do not live in argument parsing.

## Shared mechanisms

A few package-root files are used by more than one feature:

| File | Shared mechanism |
|---|---|
| `values.py` | Scalar and enum-choice parsing and validation vocabulary |
| `files.py` | Atomic small-file JSON reads and writes |
| `http_safety.py` | Public-HTTPS URL and redirect rules |
| `throttle.py` | In-process spacing for a bounded request burst |
| `local_files.py` | Default paths for private runtime files |

They contain mechanisms, not provider, matching, or reporting policy.

## Enforced boundaries

`scripts/check-imports.py` reads imports from every source file and checks the
rules a maintainer relies on:

1. Project imports cannot form a cycle.
2. Only the console entry point may depend on `cli`.
3. Provider code cannot depend on matching, reports, local history, or command
   workflows.
4. Pure record modules cannot import I/O owners.
5. Feature-package `__init__.py` files are signposts, not hidden API barrels.

This protects comprehensible ownership without assigning arbitrary numeric
layers or forcing one feature to scatter itself across unrelated folders.

## Reading paths

- For the whole application, start at `daily.py`.
- For provider-neutral analysis, start at `matching/analyze.py`.
- For the network boundary, start at `collect.py`, then `providers/http.py` and
  the selected provider directory.
- For what a person reads, start at `reports/findings.py`.
- For the public command surface, start at `cli/parser.py`, then its one dispatch
  map in `cli/__init__.py`.

Each module starts with a docstring explaining its one question. Follow a call
only when that question is the one you need to answer.

## Tests

`tests/` is organized by behavior, with shared synthetic fixtures and fakes in
`tests/support.py`. `tests/contracts/` pins the public CLI and cross-channel
report meaning during structural refactors. Compatibility fixtures protect old
watchlist and SQLite files.

Nothing in the suite contacts a provider, SMTP server, or webhook. Run the
complete gate with `scripts\test.cmd` on Windows or `python scripts/check.py`
elsewhere.
