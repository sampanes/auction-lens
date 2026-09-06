# Data acquisition

Auction Lens separates acquiring listings from analyzing them. Its canonical
JSON/CSV boundary lets the scoring and reporting engine work independently of how
data is obtained.

## Supported paths

| Path | Completeness | Automation | Status |
|---|---:|---:|---|
| Provider API or export | High | High | Preferred when available |
| Notifications sent to the operator | Varies | Medium | Suitable for provider email adapters |
| Operator-created canonical JSON/CSV | Operator-selected | Low | Supported now |
| Live HTTP fetch | High | High | Supported now |
| Synthetic fixtures | Test-only | High | Supported now |

The authorized HTTP fetcher supports configurable request limits, identifies the
operator via User-Agent, uses conditional cache headers when available, and records
attempts before connecting so a failed job cannot retry rapidly.

A fetch happens only when all four of these hold, and is refused with a specific
error otherwise:

1. `[provider] enabled = true`;
2. `[provider.acquisition] mode = "authorized_http"` and a public HTTPS `url`
   carrying no credentials;
3. the configured User-Agent variable is set and contains a contact address; and
4. the request is within the configured limits for the run mode.

Its two explicit modes control request cadence:

- `production`: a set number of provider-local daily runs with configurable spacing;
- `development`: repeated requests with an optional spacing interval.

## Nellis specifics

Nellis documents two first-party facilities that complement live fetching:

- A Watchlist under **My Account**, populated with the heart icon on an item.
- Notification Preferences under **My Account**, including auction-ending messages.

These can support an adapter that parses messages delivered to the operator's own
mailbox. Before implementing it, collect a small redacted sample set and determine
whether messages include stable inventory IDs, title, URL, price, end time, location,
and condition.

The Watchlist is not a discovery feed, so it cannot power broad anomaly detection by
itself. Comprehensive daily discovery uses the live HTTP fetch path.

As of the 2026-09-05 acquisition check, the public browse entry point is
`/browse`; the older parameterized `/search` GET route returns 404. The initial
browse response is a server-rendered navigation shell, while listing discovery
is loaded through the site's public client-side search integration. A reduced,
redacted shell fixture is retained under `fixtures/nellis/`; verbatim captures
remain ignored under `private/cache/`.

### 2026-09-06 page check

A browser check on 2026-09-06 refined the picture above. `/browse` still renders
an empty shell, but `/search?query=<terms>` does render results, so that route is
not gone; it is the older parameterized form that 404s.

The site is a Remix application. A product page at `/p/<slug>/<productId>`
carries its whole payload in `window.__remixContext`, under the route key
`routes/p.$title.$productId._index`, as a `product` object. Search results are
served through a public Algolia integration whose search key is published in the
page; Auction Lens does not use that key, and it is deliberately not recorded in
this repository.

The `product` object answers three questions this project had open:

- **Condition is six graded axes, not one word.** `grade` holds `conditionType`,
  `functionalType`, `damageType`, `assemblyType`, `packageType`, and
  `missingPartsType`, each `{id, description}`, plus a 1-5 `rating`.
- **There is a gallery.** `photos` is an array: typically a manufacturer stock
  image followed by real warehouse photographs of the actual lot.
- **`notes` is free text** written by staff, such as `verified 7/28/26`.

Two traps are worth stating out loud, because a parser gets them wrong silently:

1. **Polarity belongs to the axis, not to the word.** `description = "Yes"` is a
   green tag for `packageType` and `functionalType`, and a red tag for
   `assemblyType`. Reading the word alone inverts the meaning.
2. **`Unknown` renders no tag at all.** A lot whose `missingPartsType` is
   `Unknown` shows nothing where a red tag would sit, so on the site the absence
   of a warning does not mean the answer was good.

`id` values are per-axis, not globally unique: `5` is `New` for `conditionType`
and `Yes` for `packageType`. Match on the axis together with the description.

The observed vocabulary and three redacted samples are in
`fixtures/nellis/product-grade-samples.json`.

### Reading a saved page

The data is not plain JSON in the HTML. It arrives in a single streamed chunk at
the bottom of the response, written as a flat array of interned values plus
indexes describing the object graph: a scalar is itself, an array is a list of
indexes, and an object is `{"_<keyIndex>": valueIndex}`, so both keys and values
are indexes into that same array. A string used forty times is stored once.

`ingest/turbo_stream.py` decodes that envelope and knows nothing about auctions.
`ingest/nellis.py` is the only module that knows the provider's field names, and
turns one page into the canonical row the rest of the project already reads.

Two details that cost real debugging:

- A photo's `url` is the address that fetches. Its `fullPath` is the provider's
  own storage path, and is **relative** for the photographs it took itself, so
  reading `fullPath` puts an unopenable string in a report.
- The decoder refuses any negative marker other than the null one it has seen,
  rather than guessing. A wrong guess there silently becomes a wrong price.

Fetching and pulling are separate commands on purpose. `fetch` saves pages;
`pull` reads them. A parser can then be corrected and re-run over pages already
on disk without asking the provider for anything again.

```cmd
.venv\Scriptsuction-lens.exe pull ^
  --config config\local.toml ^
  --input private\cache\pages ^
  --output data\inbox\listings.json
```

`--input` takes one saved `.html` page or a directory of them. A page the
provider has since changed is reported by name and skipped, so one broken page
does not lose the other fifty. A reduced, redacted page carrying a real streamed
payload is kept at `fixtures/nellis/product-page.html`.

`pull` only reads pages already saved on disk. It does not fetch and it
does not bid.

Official references:

- [How do I save an item for later?](https://nellisauction-help.freshdesk.com/support/solutions/articles/68000007628-how-do-i-save-an-item-for-later-)
- [Why am I getting so many emails/texts from Nellis Auction?](https://nellisauction-help.freshdesk.com/support/solutions/articles/68000007662-why-am-i-getting-so-many-emails-texts-from-nellis-auction-)
