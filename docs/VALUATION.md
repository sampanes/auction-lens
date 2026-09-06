# Configurable valuation

Auction Lens treats market value as evidence, not as one magic number. A source
produces either price observations, research links, or both. The engine fans each
relevant listing out to every enabled source and aggregates observations only when
they have the same `basis` and currency.

Useful bases include `msrp`, `new_street`, `used_asking`, `used_sold`,
`quick_sale`, and `replacement`. Names are deliberately open-ended: a specialized
workflow may introduce another basis without changing application code.

## Source configuration

Source instances live under `[[valuation.sources]]`. The application contains no
list of marketplaces. Adding a normal research site is just a TOML edit:

```toml
[valuation]
enabled = true
currency = "USD"

[[valuation.sources]]
id = "my-new-price-site"
label = "My new price site"
adapter = "reference"
categories = ["guitar"] # Omit to use the source for every category.
url_template = "https://example.invalid/search?q={query}"
weight = 1.0
```

Templates may use `{query}`, `{brand}`, `{model}`, and `{category}`. Values are
URL-encoded. A reference adapter never contacts the site; it puts a convenient
link in the report for human research.

`weight` controls how much an observation influences the aggregate. Sample size
also contributes, but is capped so one enormous dataset cannot automatically
silence independent evidence. Every original observation remains attached to the
summary for auditability.

## Human-reviewed XML

The `xml_catalog` adapter reads simple XML. It is the preferred format for manual
entry and exports because whitespace and tabs have no semantic meaning:

```toml
[[valuation.sources]]
id = "my-reviewed-comps"
label = "My reviewed comps"
adapter = "xml_catalog"
path = "private/valuations.xml"
```

```xml
<?xml version="1.0" encoding="UTF-8"?>
<valuations>
  <entry brand="Example" model="Model 100" category="guitar">
    <price basis="new_street" low="399" typical="429" high="449"
           currency="USD" sample_size="3" confidence="0.95"
           observed_at="2026-09-05T12:00:00Z"
           url="https://example.invalid/model-100" />
    <price basis="used_sold" low="220" typical="275" high="310"
           currency="USD" sample_size="12" confidence="0.90" />
  </entry>
</valuations>
```

Entries match normalized brand and model text. `terms="alias one|alias two"` can
add title aliases. All money is parsed as decimal values, and invalid ranges fail
with an explanation.

## Declarative JSON APIs

The `http_json` adapter supports read-only JSON APIs without source-specific
Python. It uses GET only, requires HTTPS, rejects URL credentials, and caches
responses locally.

It is the only adapter that contacts a third party, so it refuses to run until
the configuration states `authorization_confirmed = true`. That line is the
operator asserting the source permits this use; nothing else can check it.

```toml
[[valuation.sources]]
id = "authorized-value-api"
label = "Authorized value API"
adapter = "http_json"
authorization_confirmed = true
endpoint = "https://api.example.invalid/values?query={query}"
items_path = "results"
basis = "used_sold"
currency = "USD"
cache_dir = "private/valuation-cache"
cache_hours = 24
timeout_seconds = 20
minimum_interval_seconds = 1
max_requests_per_run = 20

[valuation.sources.headers]
Authorization = "env:AUCTION_LENS_VALUE_API_TOKEN"
User-Agent = "env:AUCTION_LENS_VALUE_API_USER_AGENT"

[valuation.sources.fields]
low = "price.low"
typical = "price.median"
high = "price.high"
sample_size = "sales.count"
confidence = "match.confidence"
observed_at = "updated_at"
url = "research_url"
```

Field values use a deliberately small dotted-path notation such as
`results.0.price.value`. Authentication belongs in environment variables, never
in committed TOML. Network responses are cached under `private/` by default. The
adapter spaces uncached requests and caps them per run.

`cache_hours`, `timeout_seconds`, `minimum_interval_seconds`, and
`max_requests_per_run` are what keep a run polite, so a value that would switch
one of them off is refused rather than obeyed: no negative cache lifetime, no
zero request budget. Every settings mistake is reported against its full key,
as in `valuation.sources.authorized-value-api.cache_hours must be a number`.

A source failure is reported alongside successful evidence; it does not erase the
rest of the fan-out result.

## Unusual sources

When an API cannot be described with the generic JSON mapping, set `adapter` to a
Python import path such as `my_package.my_adapter:MyAdapter`. The adapter receives
the complete source configuration and implements one `collect(listing)` method.
This keeps authentication, rate limits, parsing, and tests isolated from scoring
and reporting.

Adding an adapter adds a new input mechanism. Adding a marketplace or pricing
site normally does not: it remains configuration.
