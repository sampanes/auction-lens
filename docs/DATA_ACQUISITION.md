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

Official references:

- [How do I save an item for later?](https://nellisauction-help.freshdesk.com/support/solutions/articles/68000007628-how-do-i-save-an-item-for-later-)
- [Why am I getting so many emails/texts from Nellis Auction?](https://nellisauction-help.freshdesk.com/support/solutions/articles/68000007662-why-am-i-getting-so-many-emails-texts-from-nellis-auction-)
