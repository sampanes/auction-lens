# Watchlist

SQLite remembers every listing this project has ever scored. The watchlist is
the much shorter list *you* care about, and it is a plain JSON file so you can
open it, read it, and edit it by hand.

It lives at `private/watchlist.json`, which is already ignored by git. Use
`--watchlist` on `run`, `watch`, and `watchlist` to keep more than one.

## What goes in it

Every `run` appends one price reading per reported lot. Scan once an hour and a
lot collects an hourly trail; scan once and it collects a single point. That
trail is the point of the file: it is how you see that a lot sat at $18 all
morning and then moved four times in the last twenty minutes.

Three blocks make up an entry.

| Block | Written by | Fields |
|---|---|---|
| What the provider said | every run | `title`, `url`, `photo_urls`, `estimated_retail`, `conditions`, `quality_rating`, `readings` |
| Why it was followed | scoring, merged across runs | `matched_interests` |
| What you think | only you | `my_estimate`, `verdict`, `note`, `fulfilled_interests`, `fulfillment_reviewed` |

**A run never touches the third block.** Adding a note next week cannot erase
the estimate you wrote today, and re-reading the same input file does not double
the trail: a reading is keyed by the instant it was scanned.

## Condition tags

`conditions` are the provider's own red and green tags. A lot is graded on six
separate axes, and each answer becomes one tag:

| Axis | Green | Red |
|---|---|---|
| `condition` | New | Used *(Open Box is amber)* |
| `functional` | Functional | Untested, Not Functional |
| `damage` | No Damage | Minor Damage, Major Damage |
| `missing_parts` | No Missing Parts | Missing Parts |
| `assembly` | No Assembly Needed | Assembly Required |
| `package` | In Package | No Package |

Two things about these are worth knowing, because both are easy to get backwards
(see `docs/DATA_ACQUISITION.md` for how they were found):

**The polarity belongs to the axis, not to the word.** The provider answers most
axes with `Yes` or `No`, and `Yes` is good news about packaging and bad news
about assembly. `grading.py` holds that table once so nothing downstream has to
remember it.

**An unanswered axis is amber, not silent.** The provider's own page renders
*nothing at all* where it has no answer, so on the site a lot nobody checked
looks exactly like a lot that came back clean. Auction Lens says
`Missing Parts Unknown` in amber instead. Amber tags get their own line, because
"nobody checked" is different from "we checked and it is bad".

`quality_rating` is the provider's own 1-5 star rating. It is **not** a summary
of the tags -- a `Used` lot with nothing else wrong still rates 5 -- so it is
kept as its own number. A provider that does not rate its lots leaves it null,
and the list prints `-----` rather than a zero-star row, because unrated and
rated-worst are not the same news.

## What a followed lot *is*

A lot is followed as a **thing**, not as an auction. The provider gives two ids:
`listing_id` names the auction and is what the page URL is built from, and
`inventory_id` names the physical item. An item that does not sell is relisted
under a fresh auction id, so following the auction would start a new, empty
trail every time.

Entries are therefore keyed on `inventory_id` when the provider gives one, and
fall back to `listing_id` when it does not. Each reading records the auction it
was taken in, so a trail that spans a relisting can say so:

```
[HUNTING] ***..  Example 2.1 Channel Sound Bar with ARC
  nellis:INV-77  (seen in 2 auctions)
  Bid $5.00 | Total $5.75 | -$13.00 over 2 looks since 2026-09-04 18:00
```

That is the reading worth having: it did not sell at $18, and it is back at $5.

`watch` and `watchlist` accept either id, so a person reading the file and a
person reading a URL both find the same entry.

SQLite is unaffected and stays keyed on the auction, which is what keeps
"new listing" and "price changed" meaning what they say.

## Your verdict

`verdict` is your own word, and has nothing to do with the condition tags.

| Verdict | Means |
|---|---|
| `hunting` | actively chasing this |
| `watching` | interested, not committed -- where a run starts a lot |
| `won` | got it |
| `lost` | someone else took it |
| `passed` | decided against |

The list prints them in that order, so what you are chasing is read first.

## Finite interests and explicit fulfillment

An interest with `wanted = 1` stops matching after one confirmed purchase. A
finite interest also requires a stable `id`, so renaming it later cannot detach
the purchase from its target. Three facts are deliberately kept apart:

- `matched_interests` records which configured rules surfaced the lot.
- `fulfilled_interests` records which of those wants you say a won lot satisfied.
- `fulfillment_reviewed` records that you answered that question, even when the
  answer was "none."

Auction Lens never assumes that winning a multi-match lot fulfills every match.
Assign it explicitly, repeating `--fulfills` when one purchase genuinely answers
more than one want:

```cmd
.venv\Scripts\auction-lens.exe watch ^
  --key nellis/synthetic-001 ^
  --verdict won ^
  --fulfills soundbar
```

Supplying `--fulfills` replaces the prior allocation with exactly the values on
that command and marks the question reviewed. Use `--clear-fulfillments` when
the purchase fulfilled none: it removes any prior allocation, reopens those
finite interests, and records the reviewed-none answer so reports do not keep
asking. A fulfillment counts only while the verdict is `won`, so correcting
that verdict immediately reopens the interest while retaining an auditable
record of the earlier answer.

Dropping forgets an item's whole trail, so Auction Lens refuses to drop an item
that has fulfillment allocations. First clear them (which reopens the interest),
then run the drop command:

```cmd
.venv\Scripts\auction-lens.exe watch --key PROVIDER/LISTING-ID --clear-fulfillments
.venv\Scripts\auction-lens.exe watch --key PROVIDER/LISTING-ID --verdict drop
```

The value may be the display name or stable `id` from the interest. A lot may
only fulfill a rule recorded in its own `matched_interests`; typos and unrelated
rules are rejected with the available choices. Old version-1 watchlists carry
none of these fields and continue to load. Their wins retire nothing: Auction
Lens has no honest way to infer which current interest an old purchase
satisfied. Existing version-2 allocations written before
`fulfillment_reviewed` are treated as reviewed; an entry without an allocation
or the flag remains unreviewed.

## Photos

`photo_urls` is the provider's gallery, in the order it sent them. That order
carries meaning: the first is usually the manufacturer's stock image of the
model, and **the last is a photograph of the actual lot on a warehouse shelf**.
The list shows the last one for that reason, and the accessors are named
`stock_photo_url` and `condition_photo_url` rather than by position.

## The file

```json
{
  "version": 2,
  "items": [
    {
      "uid": "nellis:INV-77",
      "source": "nellis",
      "listing_id": "synthetic-001",
      "inventory_id": "INV-77",
      "title": "Example 2.1 Channel Sound Bar with ARC",
      "url": "https://example.invalid/auction/synthetic-001",
      "photo_urls": [
        "https://example.invalid/photo/synthetic-001-stock.jpg",
        "https://example.invalid/photo/synthetic-001-shelf.jpg"
      ],
      "estimated_retail": "129.00",
      "conditions": [
        { "axis": "condition", "label": "Used", "tag": "red" },
        { "axis": "functional", "label": "Functional", "tag": "green" },
        { "axis": "damage", "label": "No Damage", "tag": "green" },
        { "axis": "missing_parts", "label": "Missing Parts Unknown", "tag": "amber" },
        { "axis": "assembly", "label": "No Assembly Needed", "tag": "green" },
        { "axis": "package", "label": "In Package", "tag": "green" }
      ],
      "quality_rating": 3,
      "my_estimate": "60",
      "verdict": "hunting",
      "note": "worth it under 40 all in",
      "matched_interests": [
        { "id": "soundbar", "name": "soundbar" }
      ],
      "fulfilled_interests": [],
      "fulfillment_reviewed": false,
      "readings": [
        {
          "scanned_at": "2026-09-04T18:00:00+00:00",
          "current_bid": "18.00",
          "total_cost": "20.70",
          "bid_count": 4,
          "listing_id": "synthetic-001"
        }
      ]
    }
  ]
}
```

Money is written as text, so a rounded float can never become the record.
`total_cost` is the bid plus buyer premium, tax, processing fee, and any saved
logistics cost -- the number you actually pay, not the number on the screen.

An absent file means an empty watchlist. An existing file with an unreadable
top level, a malformed `items` collection, or a newer format version is refused
before any write. Auction Lens 0.4 and later refuse versions newer than they
understand; do not edit a version-2 watchlist with Auction Lens 0.3, which
predates that protection.

`uid` is written for you to read and search; it is derived from `source` plus
`inventory_id` (or `listing_id` when the provider gives no item id), so editing
it in place changes nothing. A hand edit that is not
readable is reported against the entry it broke, as in
`nellis:synthetic-001: my_estimate must be a number`.

## Commands

Say what you think of a lot. Only the flags you pass are changed:

Every daily finding includes a copyable `Watch key`. It combines the provider
and listing id into the one argument the command needs:

```cmd
.venv\Scripts\auction-lens.exe watch ^
  --key nellis/synthetic-001 ^
  --verdict hunting ^
  --estimate 60 ^
  --note "worth it under 40 all in"
```

Read the list, keenest first -- by verdict, then by the provider's rating.
Red and amber tags are printed in colour when the output is a terminal, and in
plain text when it is redirected or piped, so a saved list never carries escape
sequences. The colour word is always printed either way: colour is how a line is
skimmed, never the only place the news is.

```cmd
.venv\Scripts\auction-lens.exe watchlist
.venv\Scripts\auction-lens.exe watchlist --verdict hunting
```

```
Following 2 lot(s) at private\watchlist.json.

[HUNTING] ***..  Example 2.1 Channel Sound Bar with ARC
  Watch key: nellis/synthetic-001
  Matches: soundbar [soundbar]
  Fulfills: none
  [RED] Used
  [AMBER] Missing Parts Unknown
  Retail $129.00 | My estimate $60.00 | Headroom $39.30
  Bid $18.00 | Total $20.70 | 4 bid(s) | seen 2026-09-04 18:00
  Note: worth it under 40 all in
  https://example.invalid/auction/synthetic-001
  Photo of this lot: https://example.invalid/photo/synthetic-001-shelf.jpg
```

A won match whose fulfillment has not been reviewed prints the complete
correction skeleton with that same current listing key:

```text
Fulfillment unreviewed; fix with auction-lens watch --key nellis/synthetic-001
  --verdict won --fulfills INTEREST
```

Use `--clear-fulfillments` instead when you reviewed the purchase and it
fulfilled none of the matched interests. A reviewed-none win remains visible as
`Fulfillment reviewed: fulfills none`, but no longer produces an action warning.

Headroom is your estimate minus the latest total. It goes negative once a lot
has cost more than you said it was worth, which is the number worth seeing
before bidding again.

Stop following a lot entirely, forgetting its trail:

```cmd
.venv\Scripts\auction-lens.exe watch --key nellis/synthetic-001 --verdict drop
```

## Emailing your flags

Every reported lot begins as `watching`; `hunting` is the explicit flag that
means you are actively chasing it. Send only those flags to the email account
already configured for reports:

```cmd
.venv\Scripts\auction-lens.exe watchlist ^
  --verdict hunting ^
  --config config\local.toml ^
  --email
```

The plain-text alternative contains everything the terminal view does. The HTML
version uses phone-friendly cards and includes the actual-lot photo and a direct
link. `scripts\run-daily.cmd` runs this after refreshing prices, so the scheduled
message reflects the newest scan.

Successful watchlist email is remembered in the ignored
`private/deliveries.sqlite3`. The same auction at the same latest bid is omitted
next time; a changed bid or a new listing id remains eligible. Each selection is
its own stream, so `--verdict hunting` does not mark the full watchlist or a
different verdict selection as delivered. Use `--repeat-delivery` with
`--email` for an intentional resend. The full retry and overlap contract is in
[Delivery receipts](DELIVERY.md).

Marking a lot `passed` is usually better than dropping it: the entry stays, so a
later run does not silently start following it again.

## How condition tags reach scoring

Scoring already penalises and rejects lots by condition word, and it keeps doing
exactly that. A graded lot's words come from its tags: the red and amber labels,
lowercased. So a lot tagged `Used` and `Missing Parts Unknown` arrives at scoring
as `("used", "missing parts unknown")`, and
`[condition_profiles.<name>.penalties]` in your TOML can name either of them.

The grade is the single authority when a provider sends one -- a graded listing
ignores any loose `conditions` list in the same row, so the two cannot drift.
Providers that only send words still work unchanged.

## What it deliberately does not do

Nothing prunes the file. A lot that closed months ago keeps its trail, because
looking back at what things actually went for is most of the value of having
kept it. If it ever grows past being useful, delete entries by hand -- it is
your file.
