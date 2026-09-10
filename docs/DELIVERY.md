# Delivery receipts

Auction Lens keeps a private record of what each report destination has
successfully received. This stops overlapping scheduled runs and ordinary
retries from repeating an unchanged listing.

The default ledger is `private/deliveries.sqlite3`. It is a local SQLite file
under an ignored directory; it does not belong in Git. The ledger stores a
SHA-256 fingerprint of each destination, never an email address or webhook URL,
and stores listing ids and bid revisions rather than titles, links, or photos.

## What counts as new

Receipts are specific to a report kind, channel, and destination. A findings
email, a findings webhook, and a watchlist email are independent streams. A new
recipient or webhook therefore receives its own first report, and one channel's
failure does not undo another channel's success.

For findings:

- a listing not yet accepted by that destination is included;
- the same auction id at the same bid is unchanged and omitted;
- the same auction id at a different bid is included, with the last delivered
  bid available as its comparison;
- a new auction id is new, even when it is a relisting of the same inventory
  item.

Changes to finite-interest progress or its unreviewed-win warning also make a
findings report eligible. This lets an outcome-only update arrive even when no
listing changed. The first report establishes the destination's baseline,
including when there are no findings.

Unchanged findings are removed before the report limit is applied. An old,
high-ranked listing therefore cannot occupy a slot that could carry a new or
changed one. The delivered report says how many unchanged findings were omitted
and how many eligible findings its limit held back.

Watchlist email uses the same price-revision rule, but remains separate from
findings. The full watchlist and each `--verdict` selection also have separate
receipt streams: sending `--verdict hunting` does not mark the full watchlist as
sent.

## Running it

`daily`, `run`, and emailed `watchlist` reports use the default ledger without
extra configuration. `doctor --email` or `doctor --webhook` checks an existing
ledger without contacting a provider or report destination. The file is first
created by an actual delivery attempt.

To deliberately resend the current report, add `--repeat-delivery` with the
destination flag:

```cmd
.venv\Scripts\auction-lens.exe daily --email --repeat-delivery
.venv\Scripts\auction-lens.exe watchlist --verdict hunting --email --repeat-delivery
```

Use `--delivery-ledger PATH` only when a separate receipt history is intended.
Scheduled runs that should deduplicate one another must point to the same file.
Moving or deleting the ledger starts fresh delivery history.

## Success, retries, and overlap

A receipt is committed only after the transport returns successfully. A failed
attempt records no success, so a later run can try again. When email and webhook
are both requested, each commits independently.

One run holds a short SQLite write transaction across the delivery attempt. An
overlapping run waits, then reads the first run's committed receipt instead of
sending the same unchanged report. This is intentionally **at-least-once**
delivery, not a claim of exactly once: the remote may accept a message just
before the connection fails, the process dies, or the local commit fails. In
each case the receipt is not durable and the next run may repeat that message.
Exactly-once delivery would require an idempotency guarantee from the remote
service.
