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
`.env`, updates SQLite, and emails the resulting report.

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

## Provider policy

`config/providers/nellis.example.toml` demonstrates provider-specific economics,
condition vocabulary, and rules. The project does not include automated bidding
behavior.

Keep acquisition separate from normalization and scoring so the analytical engine
remains reproducible and testable with fixtures. `docs/DATA_ACQUISITION.md`
describes the supported acquisition paths.

## Development

Run the test suite from the repository root. Nothing in it touches the network,
an SMTP server, or a real provider.

```powershell
.\.venv\Scripts\python.exe -m compileall -q src tests
.\.venv\Scripts\python.exe scripts\check-ascii.py
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

`docs/ARCHITECTURE.md` is the map: which module answers which question, the
direction dependencies are allowed to run, and where a new setting, scoring
signal, valuation source, or command is supposed to go.
