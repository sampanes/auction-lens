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

Two blocks make up an entry.

| Block | Written by | Fields |
|---|---|---|
| What the provider said | every run | `title`, `url`, `photo_urls`, `estimated_retail`, `conditions`, `quality_rating`, `readings` |
| What you think | only you | `my_estimate`, `verdict`, `note` |

**A run never touches the second block.** Adding a note next week cannot erase
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

## Photos

`photo_urls` is the provider's gallery, in the order it sent them. That order
carries meaning: the first is usually the manufacturer's stock image of the
model, and **the last is a photograph of the actual lot on a warehouse shelf**.
The list shows the last one for that reason, and the accessors are named
`stock_photo_url` and `condition_photo_url` rather than by position.

## The file

```json
{
  "version": 1,
  "items": [
    {
      "uid": "nellis:synthetic-001",
      "source": "nellis",
      "listing_id": "synthetic-001",
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
      "readings": [
        {
          "scanned_at": "2026-09-04T18:00:00+00:00",
          "current_bid": "18.00",
          "total_cost": "20.70",
          "bid_count": 4
        }
      ]
    }
  ]
}
```

Money is written as text, so a rounded float can never become the record.
`total_cost` is the bid plus buyer premium, tax, processing fee, and any saved
logistics cost -- the number you actually pay, not the number on the screen.

`uid` is written for you to read and search; it is derived from `source` plus
`listing_id`, so editing it in place changes nothing. A hand edit that is not
readable is reported against the entry it broke, as in
`nellis:synthetic-001: my_estimate must be a number`.

## Commands

Say what you think of a lot. Only the flags you pass are changed:

```cmd
.venv\Scripts\auction-lens.exe watch ^
  --source nellis ^
  --listing-id synthetic-001 ^
  --verdict hunting ^
  --estimate 60 ^
  --note "worth it under 40 all in"
```

Read the list, keenest first -- by verdict, then by the provider's rating:

```cmd
.venv\Scripts\auction-lens.exe watchlist
.venv\Scripts\auction-lens.exe watchlist --verdict hunting
```

```
Following 2 lot(s) at private\watchlist.json.

[HUNTING] ***..  Example 2.1 Channel Sound Bar with ARC
  nellis:synthetic-001
  [RED] Used
  [AMBER] Missing Parts Unknown
  Retail $129.00 | My estimate $60.00 | Headroom $39.30
  Bid $18.00 | Total $20.70 | 4 bid(s) | seen 2026-09-04 18:00
  Note: worth it under 40 all in
  https://example.invalid/auction/synthetic-001
  Photo of this lot: https://example.invalid/photo/synthetic-001-shelf.jpg
```

Headroom is your estimate minus the latest total. It goes negative once a lot
has cost more than you said it was worth, which is the number worth seeing
before bidding again.

Stop following a lot entirely, forgetting its trail:

```cmd
.venv\Scripts\auction-lens.exe watch --source nellis --listing-id synthetic-001 --verdict drop
```

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
