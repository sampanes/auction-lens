# Auction Lens

Auction Lens is a provider-agnostic, read-only toolkit for local-auction
listings. It collects authorized public listings, estimates total acquisition
cost, finds configurable interests and unusual bargains, remembers what
changed, and delivers a report a person can act on.

It never bids, logs in, bypasses access controls, or treats a provider's stated
retail value as verified market value.

## What it does

- Turns provider pages, canonical JSON, or CSV into one listing model.
- Calculates all-in cost from bid, premium, tax, fees, and known handling cost.
- Keeps ready-to-use, repair, and salvage condition policies separate.
- Counts capped and unchanged matches instead of making a short report look quiet.
- Fans price research out to any number of TOML-configured sources.
- Remembers observations, price changes, handling decisions, and followed lots.
- Retires finite interests only after an explicit human-confirmed purchase.
- Renders matching text and HTML reports, with optional email and webhook delivery.
- Caches and paces authorized requests and identifies them with a contact address.

## Quick start

Auction Lens requires Python 3.11 or newer and keeps runtime dependencies
minimal; Windows installations may add the timezone-data package. These
examples use Windows `cmd`; on macOS or Linux, use the equivalent executables
under `.venv/bin`.

```cmd
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e .
.venv\Scripts\auction-lens.exe setup
```

`setup` creates the two ignored files a public repository cannot supply:

| Local file | What belongs there |
|---|---|
| `config\local.toml` | Provider settings, interests, locations, and limits |
| `.env` | The identifying HTTP contact and optional delivery credentials |

Neither file is overwritten when setup is run again. Before making a live
request, edit the local configuration, supply a real contact address in `.env`,
and record only authorization that actually applies to you.

Read the effective human-owned choices without using the network:

```cmd
.venv\Scripts\auction-lens.exe profile
```

The focused questionnaire for durable large-item handling choices is:

```cmd
.venv\Scripts\auction-lens.exe profile --edit
```

It previews both the plain-language result and exact TOML diff, requires an
explicit confirmation, writes atomically, and keeps an ignored rollback copy.
See [Profile editing](docs/PROFILE.md).

## Try it without contacting a provider

The repository includes synthetic listings and prices using `example.invalid`
addresses:

```cmd
.venv\Scripts\auction-lens.exe run ^
  --input fixtures\synthetic\listings.json ^
  --config config\providers\nellis.example.toml
```

This exercises canonical loading, matching, pricing, history, the watchlist,
and report rendering without a network request.

## A normal day

Once local configuration and authorization are ready, one command performs the
ordinary workflow:

```cmd
.venv\Scripts\auction-lens.exe daily
```

The path is intentionally easy to follow:

```text
daily
  -> collect authorized public listings
  -> normalize them
  -> match, optionally judge, price, and rank them
  -> remember observations and followed lots
  -> build and optionally deliver reports
```

Use `--email` or `--webhook` only for a configured destination. Use
`--visiting BRANCH` for a branch already on today's route; that temporary fact
does not rewrite the stable profile.

Before an unattended run, check everything local without contacting the
provider, SMTP server, or webhook:

```cmd
.venv\Scripts\auction-lens.exe doctor --email
```

## Commands

| Command | Use it when |
|---|---|
| `setup` | A new machine needs its ignored config and `.env` |
| `profile` | You want to read or safely edit stable personal choices |
| `doctor` | You want an offline readiness check |
| `daily` | You want the whole find-to-report workflow |
| `discover` | You want canonical listings without analysis |
| `run` | You already have a canonical JSON or CSV listing file |
| `fetch` | You want to cache one authorized public page |
| `pull` | You want to reparse saved pages without another request |
| `logistics` | You want to save or clear one handling decision |
| `watch` | You want to record an opinion, estimate, or fulfillment |
| `watchlist` | You want to read or email followed lots |
| `sold` | You want closing-price floors from observation history |

Run `auction-lens COMMAND --help` for flags and defaults. The command parser is
the sole authority for that public surface.

## Configuration, without hidden rules

`config\local.toml` is the source of truth. The guided profile editor changes
only the small set it can explain and reverse safely; everything advanced stays
ordinary TOML.

The public [Nellis example](config/providers/nellis.example.toml) documents the
available sections in context:

- provider identity, authorization, caching, and pacing;
- auction economics and acceptable pickup locations;
- general and purpose-specific condition policy;
- interests, including finite quantities and salvage uses;
- optional local judging and configurable price sources;
- report length, email, and webhook choices.

Defaults live in the configuration records and command parser, not in this
README. Use `profile` to see what the selected file means after defaults and
condition profiles are applied.

## Clothing that actually fits

Clothing is the one category where the right product at the right price is
still useless. Declare each person once, then let any interest name them:

```toml
[[people]]
name = "sam"
sizes = ["M", "9.5", "32x30"]
styles = ["mens", "unisex"]

[[interests]]
name = "work jacket"
any_terms = ["carhartt", "work jacket", "canvas jacket"]
fits = "sam"
```

Sizes belong to the person rather than to the want, so the same answer governs
a jacket rule and a boots rule and the two cannot drift apart.

Two silences are treated as silence rather than as a mismatch. A title that
never states a size is never refused for its size, because warehouse titles
routinely omit it and refusing them would hide more real finds than the check
saves. A size chart nobody filled in cannot refuse anything either: letter
sizes, numeric shoe sizes, and waist-by-inseam are three unrelated
measurements, and writing down a shirt size does not declare a waist wrong.

Naming a person who was never declared is an error, not a silent no-op -- a
typo that quietly disabled the check would look exactly like a check that
passed. Run `profile` to read back who each interest is shopping for.

## Reports and local memory

Report construction decides what to say once. Plain text and HTML then render
the same facts, including omissions, open logistics questions, value evidence,
and finite-interest progress. HTML email can show product and actual-lot images;
the images remain remote links rather than becoming large attachments.

Local runtime state is ignored by Git:

- SQLite history records observations, price movements, and handling decisions.
- The JSON watchlist keeps followed lots, price trails, verdicts, and explicit
  fulfillment decisions in a hand-readable format.
- A separate SQLite delivery ledger remembers which revision each opaque
  destination fingerprint accepted.

Observation history and delivery history answer different questions. A listing
can be old to the collector but new to an email recipient. Unchanged revisions
are removed before a destination's report cap; failed deliveries remain
eligible, and `--repeat-delivery` is the explicit resend override. See
[Delivery receipts](docs/DELIVERY.md).

Closing-price output is deliberately a floor, not a claimed sale price: it can
only report the last bid observed before a listing disappeared. The `sold`
command names how close that observation was to closing.

## Email and Windows scheduling

Guided SMTP setup keeps the password hidden and writes credentials only to the
ignored `.env` file:

```cmd
.venv\Scripts\auction-lens.exe setup --email
.venv\Scripts\auction-lens.exe doctor --email
```

Gmail requires 2-Step Verification and an App Password. Follow the exact
[Gmail setup and proof](docs/GMAIL.md), including removing display spaces from
the App Password before saving it.

On Windows, `scripts\run-daily.cmd` is the unattended runner. The schedule is
owned in exactly one place: the declarations at the top of
`scripts\schedule.cmd`.

```cmd
scripts\schedule.cmd status
scripts\schedule.cmd install
scripts\schedule.cmd remove
```

`install` replaces tasks with the same names instead of duplicating them.
Change run times in that script, not in documentation or a second scheduler.

## Provider boundary

The included Nellis adapter exists because that provider granted limited,
conditional access for one personal deployment. That permission is informal,
revocable, and non-transferable. This repository and its example configuration
do not grant anyone else permission.

Only enable acquisition after receiving authorization that covers your own use
and each request shape you intend to send. Auction Lens requires an identifiable
User-Agent, local caching, request pacing, explicit run limits, public pages,
and read-only behavior. It does not automate login, bidding, or interaction
behind an access wall.

See [Data acquisition](docs/DATA_ACQUISITION.md) for the enforced boundary and
[the authorization request template](docs/NELLIS_AUTHORIZATION_REQUEST.md) for a
plain-language way to ask a provider first.

## Find your way around

The source folders are named for auction features. Start with the question you
have rather than memorizing a dependency diagram:

- [Code map](docs/CODE_MAP.md) - which file answers a concrete question.
- [Architecture](docs/ARCHITECTURE.md) - workflow ownership and dependency rules.
- [Conventions](docs/CONVENTIONS.md) - how code should read and where additions go.
- [Simplicity](docs/SIMPLICITY.md) - the standard every design change is judged by.
- [Changelog](CHANGELOG.md) - what each tagged release changed for a person.

Operator guides:

| Question | Guide |
|---|---|
| How do I edit practical limits safely? | [Profile](docs/PROFILE.md) |
| How may listing pages be acquired? | [Data acquisition](docs/DATA_ACQUISITION.md) |
| How do I add price evidence? | [Valuation](docs/VALUATION.md) |
| What is stored about followed lots? | [Watchlist](docs/WATCHLIST.md) |
| Why was a delivery omitted or repeated? | [Delivery](docs/DELIVERY.md) |
| How do I configure Gmail? | [Gmail](docs/GMAIL.md) |
| What is implemented or still open? | [Roadmap](docs/ROADMAP.md) |

## Development

Install the pinned development tools and run the same gate used by CI:

```cmd
.venv\Scripts\python.exe -m pip install -e ".[dev]"
scripts\test.cmd
```

The gate compiles the package, checks portable ASCII, enforces import
boundaries, runs Ruff, and executes the test suite. Tests use synthetic or
redacted fixtures and fake transports; they do not contact a provider, SMTP
server, or webhook.

Before changing code, read [Simplicity](docs/SIMPLICITY.md). Before publishing,
inspect the staged files as well as the diff: `.env`, `config/local.toml`,
provider caches, databases, watchlists, delivery receipts, and agent notes are
local-only.
