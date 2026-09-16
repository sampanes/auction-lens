# Code map

Start with the question you are trying to answer. This page points to the few
files that own that answer; [Architecture](ARCHITECTURE.md) explains the broader
dependency rules.

## Where does a normal day start?

- [`daily.py`](../src/auction_lens/daily.py) is the operator workflow. `daily`
  collects before analysing; `run` analyses a file already on disk.
- [`collect.py`](../src/auction_lens/collect.py) owns fetch, discovery, and
  reparsing saved pages into canonical listings.
- [`matching/analyze.py`](../src/auction_lens/matching/analyze.py) is the same
  analysis without command-line concerns.
- [`reports/send.py`](../src/auction_lens/reports/send.py) plans each requested
  delivery, sends it, and records success.

Read those four in that order for the shortest end-to-end tour.

## How does provider data enter safely?

- [`providers/http.py`](../src/auction_lens/providers/http.py) enforces explicit
  authorization, identification, caching, pacing, redirect safety, and run limits.
- [`providers/registry.py`](../src/auction_lens/providers/registry.py) maps each
  configured provider id to the four site-specific collection operations.
- [`providers/search_terms.py`](../src/auction_lens/providers/search_terms.py)
  decides which configured phrases a discovery run asks for.
- [`providers/nellis/discover.py`](../src/auction_lens/providers/nellis/discover.py)
  owns the provider-specific search and branch-session requests.
- [`providers/nellis/parse.py`](../src/auction_lens/providers/nellis/parse.py)
  translates saved provider pages into neutral rows.
- [`collect.py`](../src/auction_lens/collect.py) writes those rows to the
  canonical file consumed by the rest of the program.

The policy and permission boundary is documented in
[Data acquisition](DATA_ACQUISITION.md). Provider code never decides whether a
listing is desirable.

## What is a listing inside the program?

- [`listings/model.py`](../src/auction_lens/listings/model.py) defines the
  provider-neutral listing and its stable identity.
- [`listings/files.py`](../src/auction_lens/listings/files.py) reads canonical
  JSON and CSV and removes duplicate observations.
- [`listings/conditions.py`](../src/auction_lens/listings/conditions.py) turns
  provider condition answers into consistent, readable evidence.

## Why did a listing appear, disappear, or rank here?

- [`matching/evaluate.py`](../src/auction_lens/matching/evaluate.py) applies
  common gates, total cost, broad bargain discovery, and candidate creation.
- [`matching/interests.py`](../src/auction_lens/matching/interests.py) answers
  whether a title satisfies one configured interest and condition policy.
- [`matching/text.py`](../src/auction_lens/matching/text.py) owns literal phrase
  and accessory-context matching.
- [`matching/sizes.py`](../src/auction_lens/matching/sizes.py) reads the size and
  audience out of a title and decides whether one person could wear it.
- [`matching/judge.py`](../src/auction_lens/matching/judge.py) optionally asks a
  local model whether a broad word match is actually the requested thing; an
  explicit mismatch is down-ranked and labelled, never deleted.
- [`matching/logistics.py`](../src/auction_lens/matching/logistics.py) turns size,
  seller assistance, and saved handling decisions into a practical status.
- [`matching/model.py`](../src/auction_lens/matching/model.py) defines candidate
  scores, ranking, sections, and caps.
- [`matching/progress.py`](../src/auction_lens/matching/progress.py) derives
  active and retired finite interests from explicit outcomes.
- [`matching/searches.py`](../src/auction_lens/matching/searches.py) finds a few
  provider search phrases for crowded report sections.

[`tests/test_matching.py`](../tests/test_matching.py),
[`tests/test_judging.py`](../tests/test_judging.py), and
[`tests/test_sections.py`](../tests/test_sections.py) are executable examples of
those decisions.

## How is market value estimated?

- [`pricing/model.py`](../src/auction_lens/pricing/model.py) keeps price evidence
  and provenance together.
- [`pricing/value.py`](../src/auction_lens/pricing/value.py) selects configured
  sources and combines comparable observations.
- [`pricing/sources.py`](../src/auction_lens/pricing/sources.py) defines the small
  adapter contract and shared source settings.
- [`pricing/reference.py`](../src/auction_lens/pricing/reference.py),
  [`pricing/xml_catalog.py`](../src/auction_lens/pricing/xml_catalog.py), and
  [`pricing/http_json.py`](../src/auction_lens/pricing/http_json.py) are the
  built-in input mechanisms.

Most new marketplaces are configuration, not code. See
[Configurable valuation](VALUATION.md).

## What exactly reaches a report?

- [`reports/records.py`](../src/auction_lens/reports/records.py) defines the
  rendering-independent facts shared across channels.
- [`reports/findings.py`](../src/auction_lens/reports/findings.py) turns ranked
  candidates into those reader-facing facts.
- [`reports/text.py`](../src/auction_lens/reports/text.py) and
  [`reports/html.py`](../src/auction_lens/reports/html.py) render the same report.
- [`reports/email.py`](../src/auction_lens/reports/email.py) builds and submits
  secure SMTP messages.
- [`reports/webhook.py`](../src/auction_lens/reports/webhook.py) builds and posts
  the compact chat form.

[`tests/contracts/test_report_delivery_contract.py`](../tests/contracts/test_report_delivery_contract.py)
pins the important meaning across text, HTML, email, and webhook output.

## Why was an unchanged item not sent again?

- [`reports/delivery.py`](../src/auction_lens/reports/delivery.py) compares a
  proposed report with revisions already accepted by one destination.
- [`reports/receipts.py`](../src/auction_lens/reports/receipts.py) persists only
  opaque destination fingerprints and successful revisions.
- [`reports/send.py`](../src/auction_lens/reports/send.py) keeps planning,
  transport, and receipt recording in the right order.
- [Delivery receipts](DELIVERY.md) explains retries and the explicit resend.

## What is remembered locally?

- [`history/database.py`](../src/auction_lens/history/database.py) owns the
  SQLite connection, schema, and transaction boundary.
- [`history/observations.py`](../src/auction_lens/history/observations.py) records
  what changed between listing observations.
- [`history/logistics.py`](../src/auction_lens/history/logistics.py) stores
  per-listing handling decisions.
- [`history/closing_prices.py`](../src/auction_lens/history/closing_prices.py)
  reads and explains closing-price floors.
- [`watchlist/model.py`](../src/auction_lens/watchlist/model.py) defines human
  verdicts, price trails, and fulfillment evidence.
- [`watchlist/store.py`](../src/auction_lens/watchlist/store.py) preserves the
  ignored, hand-readable JSON file.
- [`watchlist/report.py`](../src/auction_lens/watchlist/report.py) renders that
  history for a terminal or email.

Old local formats are protected by
[`tests/test_persisted_compatibility.py`](../tests/test_persisted_compatibility.py)
and [`fixtures/compatibility`](../fixtures/compatibility/).

## How does feedback become a proposal?

- [`feedback/model.py`](../src/auction_lens/feedback/model.py) defines the small
  reaction vocabulary, immutable evidence, patterns, and proposal records.
- [`feedback/record.py`](../src/auction_lens/feedback/record.py) turns a known
  watched item into one feedback or correction event.
- [`feedback/store.py`](../src/auction_lens/feedback/store.py) keeps the ignored
  append-only JSON history and derives each current effective reaction.
- [`feedback/review.py`](../src/auction_lens/feedback/review.py) counts distinct
  items and permits only a clean, evidence-backed narrowing of price limits.
- [`feedback/artifacts.py`](../src/auction_lens/feedback/artifacts.py) saves an
  immutable private proposal; it never edits TOML.
- [`cli/feedback.py`](../src/auction_lens/cli/feedback.py) resolves Watch keys
  against the watchlist and presents the workflow at the command line.

[Feedback-assisted tuning](FEEDBACK.md) explains the labels, corrections, and
the boundary between evidence and configuration.

## Where do I change my preferences?

- `config/local.toml` is the ignored source of truth on an operator's machine.
- [`config/`](../src/auction_lens/config/) owns the typed configuration,
  loading boundary, plain-language profile, and reversible guided edit.
- [`config/providers/nellis.example.toml`](../config/providers/nellis.example.toml)
  is the public, non-authorizing example of every supported section.
- [`setup.py`](../src/auction_lens/setup.py) creates missing private files without
  overwriting them; [`doctor.py`](../src/auction_lens/doctor.py) checks readiness.

Credentials and contact values belong in ignored `.env`, not in TOML.

## How does a command name reach code?

- [`cli/parser.py`](../src/auction_lens/cli/parser.py) is the sole list of public
  commands, flags, help, and defaults.
- [`cli/__init__.py`](../src/auction_lens/cli/__init__.py) maps each command word
  to one function and translates expected operator errors into exit codes.
- The large workflows have plain top-level names: `setup.py`, `doctor.py`,
  `collect.py`, and `daily.py`.
- The remaining [`cli/`](../src/auction_lens/cli/) modules adapt inherently
  interactive watchlist and logistics actions plus the local `sold` query.

The exact public surface is frozen by
[`tests/contracts/test_cli_surface.py`](../tests/contracts/test_cli_surface.py).

## Where does a new thing go?

| Change | First home |
|---|---|
| Another listing provider | `providers/<name>/`, emitting the existing listing model |
| Another configured pricing site | Local TOML using an existing `pricing/` adapter |
| A genuinely new pricing protocol | A focused adapter in `pricing/` |
| A new matching rule | The matching file that already owns that kind of decision |
| A new report fact | `reports/records.py`, then `findings.py` and every renderer |
| A new persisted field | Its subject model/store plus a compatibility fixture |
| A new feedback label or proposal kind | `feedback/`, with its operator meaning documented first |
| A new command | `cli/parser.py`, its feature workflow, then the one dispatch map |

[Conventions](CONVENTIONS.md) contains the complete change checklist.

## What are the small files at the package root?

- [`values.py`](../src/auction_lens/values.py) is shared scalar parsing and validation.
- [`files.py`](../src/auction_lens/files.py) is atomic small-file JSON I/O.
- [`http_safety.py`](../src/auction_lens/http_safety.py) is the public-HTTPS and redirect boundary.
- [`throttle.py`](../src/auction_lens/throttle.py) spaces requests within one run.
- [`local_files.py`](../src/auction_lens/local_files.py) is the one authority for default private paths.

These modules hold shared mechanisms only; auction policy remains in the named
feature directories.
