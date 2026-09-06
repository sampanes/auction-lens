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
| What the provider said | every run | `title`, `url`, `image_url`, `estimated_retail`, `readings` |
| What you think | only you | `my_estimate`, `state`, `stars`, `note` |

**A run never touches the second block.** Adding a star next week cannot erase
the estimate you wrote today, and re-reading the same input file does not double
the trail: a reading is keyed by the instant it was scanned.

## States and tags

`state` is one of five words, and every state has a colour so a long list can be
skimmed before it is read.

| State | Tag | Means |
|---|---|---|
| `hunting` | green | actively chasing this |
| `won` | green | got it |
| `watching` | amber | interested, not committed -- the state a run starts a lot in |
| `passed` | red | decided against |
| `lost` | red | someone else took it |

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
      "image_url": "",
      "estimated_retail": "129.00",
      "my_estimate": "60",
      "state": "hunting",
      "tag": "green",
      "stars": 4,
      "note": "worth it under 40 all in",
      "readings": [
        {
          "scanned_at": "2026-09-04T18:00:00+00:00",
          "current_bid": "18.00",
          "total_cost": "20.70",
          "bid_count": 4
        },
        {
          "scanned_at": "2026-09-04T19:00:00+00:00",
          "current_bid": "25.00",
          "total_cost": "28.75",
          "bid_count": 7
        }
      ]
    }
  ]
}
```

Money is written as text, so a rounded float can never become the record.
`total_cost` is the bid plus buyer premium, tax, processing fee, and any saved
logistics cost -- the number you actually pay, not the number on the screen.

`uid` and `tag` are written for you to read and search; both are derived, from
`source` plus `listing_id` and from `state`, so editing either in place changes
nothing. A hand edit that is not readable is reported against the entry it
broke, as in `nellis:synthetic-001: my_estimate must be a number`.

## Commands

Say what you think of a lot. Only the flags you pass are changed:

```cmd
.venv\Scripts\auction-lens.exe watch ^
  --source nellis ^
  --listing-id synthetic-001 ^
  --state hunting ^
  --stars 4 ^
  --estimate 60 ^
  --note "worth it under 40 all in"
```

Read the list, keenest first -- green before amber before red, then by stars:

```cmd
.venv\Scripts\auction-lens.exe watchlist
.venv\Scripts\auction-lens.exe watchlist --state hunting
```

```
Following 2 lot(s) at private\watchlist.json.

[GREEN] hunting ****.  Example 2.1 Channel Sound Bar with ARC
  nellis:synthetic-001
  Retail $129.00 | My estimate $60.00 | Headroom $31.25
  Bid $25.00 | Total $28.75 | 7 bid(s) | +$7.00 over 2 looks since 2026-09-04 18:00 | seen 2026-09-04 19:00
  Note: worth it under 40 all in
  https://example.invalid/auction/synthetic-001
```

Headroom is your estimate minus the latest total. It goes negative once a lot
has cost more than you said it was worth, which is the number worth seeing
before bidding again.

Stop following a lot entirely, forgetting its trail:

```cmd
.venv\Scripts\auction-lens.exe watch --source nellis --listing-id synthetic-001 --state drop
```

Marking a lot `passed` is usually better than dropping it: the entry stays, so a
later run does not silently start following it again.

## What it deliberately does not do

Nothing prunes the file. A lot that closed months ago keeps its trail, because
looking back at what things actually went for is most of the value of having
kept it. If it ever grows past being useful, delete entries by hand -- it is
your file.
