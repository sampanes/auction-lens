# Auction Lens

Auction Lens is a provider-agnostic toolkit for normalizing local-auction
listings, estimating acquisition costs, ranking potentially interesting deals,
and delivering configurable reports.

It is deliberately **read-only**: Auction Lens does not place bids. It works from
canonical JSON or CSV, live HTTP sources, or a combination of both.

## What it does

- Normalizes listings into a small, documented domain model.
- Estimates total cost from bid, buyer premium, tax, and processing fees.
- Separates explicit interest rules from broad retail-ratio anomalies.
- Applies condition policy per intended use, so broken salvage is not treated
  like broken ready-to-use equipment.
- Fans listings out to any number of TOML-declared valuation sources.
- Keeps MSRP, asking prices, sold prices, and replacement value separate.
- Filters pickup locations with case-insensitive configured names.
- Enforces configurable HTTP request limits to avoid unnecessary load.
- Remembers observations and price changes in SQLite.
- Renders plain-text and HTML reports and can send them over SMTP.

## Quick start

Python 3.11 or newer is required. The application uses only the standard library;
on Windows, installation also supplies the IANA time-zone database used for
provider-local request limits.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\auction-lens.exe run `
  --input fixtures\synthetic\listings.json `
  --config config\providers\nellis.example.toml `
  --database data\auction-lens.sqlite3
```

The bundled records are synthetic and use `example.invalid` URLs. They exercise
the Nellis-shaped configuration without accessing Nellis Auction.

A live deployment can fetch configured pages with:

```powershell
.\.venv\Scripts\auction-lens.exe fetch --config config\local.toml
```

The fetcher uses an identifiable User-Agent, records attempts before connecting,
and caches responses atomically. Production mode caps requests per provider-local
day with a configurable interval. Development mode permits repeated requests with
an optional spacing delay.

## Email reports

Enable `[reports.email]` in a local configuration, then set the named environment
variables for the SMTP host, username, password, sender, and recipient. Keep the
local configuration and credentials out of version control.

By default the CLI loads non-empty values from an ignored `.env` file in the
working directory. Existing process environment variables take precedence. Gmail
accounts normally require an app password rather than the ordinary account password.

```powershell
.\.venv\Scripts\auction-lens.exe run --input listings.json --config config\local.toml --email
```

Run that command from Windows Task Scheduler, cron, or another scheduler to send
a periodic digest. Repeated observations are retained so reports can distinguish
new listings from changed prices.

For Windows, `scripts/run-daily.ps1` is the ready-to-schedule entry point. It reads
an ignored `data/inbox/listings.json`, uses the ignored personal configuration and
`.env`, updates SQLite, and emails only lots you marked `hunting`.

## Getting real listings

One command asks the provider's search and writes listings ready to score:

```powershell
.\.venv\Scripts\auction-lens.exe discover `
  --config config\local.toml `
  --output data\inbox\listings.json
```

A search page carries the complete data for every lot it lists, so one request
describes a whole page of them rather than one. Terms come from `--search`, or
from `[provider.acquisition] searches`, or failing both from the `any_terms` of
your `[[interests]]` -- so what you want is written down once.

A whole discovery run counts as a single attempt against the configured daily
limit, and the searches inside it are spaced apart. Each term's page is cached
and revalidated, so an unchanged page costs nothing.

Some providers scope their catalogue to one branch and choose it by session
rather than by URL, so `[provider.acquisition] session_url` and `session_fields`
say which branch a run is shopping. Without it the site serves its default city,
and the results look perfectly real while being hundreds of miles away.

### Near and far branches

Distance is a fact about you, not about a lot, so it is not scored. A branch you
pass anyway and one half an hour in the wrong direction are both acceptable, but
not on the same terms:

```toml
[locations]
allowed = ["phoenix", "mesa"]
far = ["phoenix"]
far_minimum_score = 90
```

Everything at a near branch is reported as usual. A lot at a far branch is
reported only if it scores at least `far_minimum_score` -- good enough to
justify the drive rather than merely good.

Fetching and pulling are separate steps. `fetch` saves a provider page; `pull`
reads saved pages into the canonical file `run` analyses. Keeping them apart
means a parser can be corrected and re-run over pages already on disk without
asking the provider again.

```powershell
.\.venv\Scripts\auction-lens.exe pull `
  --config config\local.toml `
  --input private\cache\pages `
  --output data\inbox\listings.json
```

A pulled lot carries everything the page states: the six condition tags, the
provider's quality rating, and the photo gallery. It is then indistinguishable
from a hand-written listing, so scoring, valuation, and the watchlist need to
know nothing about where it came from.

## Canonical input

JSON input is either a list or an object with a `listings` list. CSV remains
supported for imports, while editable valuation catalogs use XML. Required fields
are `source`, `listing_id`, `title`, `url`, and `current_bid`. Common optional
fields include:

```json
{
  "source": "provider-id",
  "listing_id": "stable-id",
  "title": "Example listing",
  "brand": "Example",
  "model": "Model 100",
  "category": "guitar",
  "handling_weight_lb": "148",
  "package_dimensions_in": ["70", "31", "45"],
  "loading_assistance": ["forklift"],
  "url": "https://example.invalid/listing/1",
  "current_bid": "12.00",
  "estimated_retail": "100.00",
  "bid_count": 3,
  "ends_at": "2026-09-04T23:30:00Z",
  "location": "Example Warehouse",
  "conditions": ["used"],
  "image_url": "https://example.invalid/image.jpg",
  "buyer_premium_rate": "0.15",
  "observed_at": "2026-09-04T22:00:00Z"
}
```

Money enters through decimal strings and is stored without binary floating-point
rounding. Provider-reported retail values are treated as ranking signals, not as
verified market value.

## Interests and valuation

Interests describe *why* an item is useful. Each `[[interests]]` rule has its own
condition policy, allowing one known-broken listing to fail a `purpose = "use"`
rule while matching a carefully constrained `purpose = "salvage"` rule. Broad
anomaly discovery has a separate condition policy as well.

Valuation sources are ordinary `[[valuation.sources]]` TOML entries. Built-in
adapters support human-reviewed XML catalogs, research-link templates, and
authorized read-only JSON APIs. Custom adapters use a normal Python import path,
so unusual integrations remain isolated. See [the valuation guide](docs/VALUATION.md)
for the configuration and XML formats.

## Contextual logistics

Listings may provide a handling weight, package dimensions, and seller loading
assistance. The generic `[logistics]` thresholds do not describe a person's
friends, vehicles, or home. They only decide when a promising listing needs a
handling question.

Seller assistance resolves the origin-loading stage. It does not silently assume
that an item fits the transport or can be unloaded at its destination. A report
therefore turns a heavy forklift-loaded lot into a focused question instead of a
blanket rejection.

Save a decision for one listing in the same ignored SQLite database:

```powershell
.\.venv\Scripts\auction-lens.exe logistics `
  --source provider-id `
  --listing-id stable-id `
  --status feasible `
  --added-cost 25 `
  --note "Handling arranged"
```

Use `--status infeasible` to suppress the listing or `--status clear` to ask
again. Added logistics cost participates in configured price ceilings. The future
profile questionnaire is deliberately separate; see [the roadmap](docs/ROADMAP.md).

## Watchlist

Every `run` appends one price reading -- time, bid, total cost, bid count -- for
each reported lot to an ignored `private/watchlist.json`. Scan hourly and a lot
collects an hourly trail; scan once and it collects a single point.

Alongside the trail it keeps what the provider says about the lot: the six
condition tags it grades (`Used`, `Assembly Required`, `Missing Parts` and the
rest, each red, amber, or green), its own 1-5 quality rating, and the photo
gallery -- whose last image is the photograph of the actual lot rather than the
manufacturer's stock shot.

On top of that you record what *you* think: your own estimate, a verdict, and a
note. A run never overwrites any of it.

```powershell
.\.venv\Scripts\auction-lens.exe watch `
  --source nellis `
  --listing-id synthetic-001 `
  --verdict hunting `
  --estimate 60 `
  --note "worth it under 40 all in"

.\.venv\Scripts\auction-lens.exe watchlist
```

The list prints keenest first, with headroom -- your estimate minus the latest
total -- so a lot that has already cost more than you said it was worth says so.
See [the watchlist guide](docs/WATCHLIST.md) for the file format and for the two
ways a condition grade is easy to read backwards.

Email only the lots you explicitly flagged as `hunting`:

```powershell
.\.venv\Scripts\auction-lens.exe watchlist `
  --verdict hunting `
  --config config\local.toml `
  --email
```

The email is a compact set of phone-friendly cards with price, headroom,
condition concerns, the actual-lot photo, and a direct listing link.

## Provider policy

`config/providers/nellis.example.toml` demonstrates provider-specific economics,
condition vocabulary, and rules. The project does not include automated bidding
behavior.

Keep acquisition separate from normalization and scoring so the analytical engine
remains reproducible and testable with fixtures. `docs/DATA_ACQUISITION.md`
describes the supported acquisition paths.

## Development

One command runs everything CI runs, in the same order: compiling, the ASCII
check, the module-layering check, the linter, and the tests. Nothing in the
suite touches the network, an SMTP server, or a real provider.

Install the development tools once, then run the check wrapper:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
scripts\test.cmd
```

`docs/ARCHITECTURE.md` is the map: which module answers which question, and the
direction dependencies are allowed to run -- a layering that is checked, not
just described. `docs/CONVENTIONS.md` is the house style: where a validation
rule belongs, when a closed set of words becomes an enum, when to split a module
and when not to, and where a new setting, scoring signal, valuation source, or
command is supposed to go.
