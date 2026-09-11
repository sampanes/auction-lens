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
- Remembers successful deliveries separately, so unchanged listings do not
  repeat across overlapping runs.
- Retires finite interests after an explicitly assigned win, and can reopen them.
- Renders plain-text and photo-backed HTML reports and can send them over SMTP.

## Start here

Python 3.11 or newer. The application uses only the standard library; on Windows,
installation also supplies the IANA time-zone database used for provider-local
request limits.

```cmd
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e .
.venv\Scripts\auction-lens.exe setup
```

`setup` writes the two files git cannot carry, because one holds what you want
and the other holds your secrets:

| file | what it is | what you must edit |
| --- | --- | --- |
| `.env` | ignored settings and credentials | `AUCTION_LENS_HTTP_USER_AGENT` must contain a real contact address. Nothing will make a request without it. Mail and webhook secrets live here too, never in the config. |
| `config\local.toml` | ignored personal configuration | `[locations] allowed`, and the `[[interests]]` describing what you actually want |

Neither is ever overwritten, so `setup` is safe to re-run.

Then one command does the day's work -- find lots, score them, report what
matters:

```cmd
.venv\Scripts\auction-lens.exe daily
```

Add `--email` to send it. Every command defaults to `config\local.toml`, so the
`--config` flag is only needed when pointing somewhere else.

To try the scoring without contacting any provider, the bundled records are
synthetic and use `example.invalid` addresses:

```cmd
.venv\Scripts\auction-lens.exe run ^
  --input fixtures\synthetic\listings.json ^
  --config config\providers\nellis.example.toml
```

### The other commands

`daily` is `discover` followed by `run`. Both remain separate because each is
useful alone -- a parser can be corrected and re-run without asking the provider
again -- but neither has to be typed day to day.

| command | when you want it |
| --- | --- |
| `setup` | first run on a new machine |
| `profile` | read or safely edit practical limits in plain language |
| `doctor` | check authorization, configuration, and delivery settings without network access |
| `daily` | every day: find, score, report |
| `discover` | find lots and write them, without scoring |
| `fetch` / `pull` | save one page; read saved pages back |
| `run` | score a listing file you already have |
| `watch` / `watchlist` | record what you think of a lot; read what you are following |
| `sold` | see what closed lots were last going for |
| `logistics` | record how a bulky lot would be collected |

## Read or edit your profile

The TOML is the source of truth, but it does not have to be read like source
code. One command translates its stable operator choices into plain language:

```cmd
.venv\Scripts\auction-lens.exe profile
```

It explains each interest, whether it is ongoing or finite, its effective
condition rules, the general bargain rule, locations, large-item handling,
report length, and whether valuation is active. Empty settings are stated rather
than skipped. The command
does not read credentials, listings, history, or the network, and it never
writes the configuration.

Temporary circumstances remain temporary. For example, a branch already on
today's route is supplied to `daily --visiting`; it is not silently saved into
the stable profile.

The guided editor starts with the three durable answers that settle how large
lots are treated:

```cmd
.venv\Scripts\auction-lens.exe profile --edit
```

It asks whether large lots should be kept with a handling question, kept
without one, or rejected, plus the published weight and dimension at which
special planning begins. It does not ask whether you own a truck or can call a
friend: those are changing circumstances, while the action Auction Lens should
take is the useful configuration.

Before writing, the command shows the resulting plain-language profile and the
exact TOML lines that would change. Only an explicit `y` or `yes` applies them.
The original file is kept beside it as an ignored `.previous` snapshot, and can
be previewed and restored with:

```cmd
.venv\Scripts\auction-lens.exe profile --restore
```

The editor never reads `.env`, runtime history, listings, or the network. More
advanced rules remain ordinary, human-editable TOML. See
[Profile editing](docs/PROFILE.md) for the full safety contract.

## How long the report is

A sweep can match hundreds of lots and be right about all of them. The scoring
bars decide what is worth reporting; this decides how much of it a person is
going to read:

```toml
[reports]
max_items = 30
```

The best 30 by priority, so the weights on your interests choose what survives
the cut rather than the cap choosing for them. Leave the key out for all of
them. Whatever is held back is counted out loud, because a short report and a
quiet day should never look alike:

```
Showing the best 30; 766 more matched. Raise reports.max_items to see them.
```

The local report applies the cap to today's ranking. Each outbound destination
first removes unchanged listings it has already received, then applies the same
configured limit (and any smaller transport limit). That keeps an old top result
from occupying a slot that could carry a lower-ranked new one. The delivered
report counts both kinds of omission; the exact rules are in
[Delivery receipts](docs/DELIVERY.md).

## What gets read first

```toml
[reports]
order = "retail"
```

`priority` is the default: how good a lot is, scaled by how much you said you
wanted it. `retail` answers the other question -- what is the most valuable
thing here -- which is the one you ask when you are about to drive out and
collect, and it ignores how well the lot scored.

Reading order only. Which lots are worth reporting was already settled by the
scoring bars, and preferring to see the dearest thing first must not quietly
change what reached the page. A lot with no stated retail reads last, because
an unknown value is not a large one.

## When a lot closes

Every report line says when bidding on that lot ends:

```
Bid: $8.00 | Estimated total: $9.20 | Retail: $301.15
Closes: Wed 21:57 MST | Location: Mesa | Conditions: none listed
```

The time is the provider's, not yours. A lot closes at the auction house, so
the clock that matters is the one hanging there, and it is named on every line
because a report gets read on a phone in some other state. Which zone that is
comes from the one place that already had to know:

```toml
[provider.acquisition]
timezone = "America/Phoenix"
```

That key already decided which local day a request quota falls in. Reusing it
means there is no second timezone setting to disagree with the first.

A lot that publishes no closing time simply says nothing about one, rather than
being given an invented deadline. Every lot seen so far publishes one, so if
that line goes missing across the board, the page shape changed.

## What things actually go for

Every judgement so far has been made against the provider's own estimated
retail, which is not a price anyone paid. What a lot really sold for is harder
to come by than it sounds: no hammer price is published, and a closed lot drops
off the pages this reads, so the last look is always one look too early.

That leaves a floor rather than a sale price, and `sold` says exactly that:

```
$ auction-lens sold --match "miter saw"
1 lot(s) were last looked at within 30 minute(s) of closing.
Each price is a floor: the lot sold for at least this much.

  at least $159 of $739 estimated retail (22%), 18 bid(s), seen 1m before it closed
    closed Wed 09 Sep 18:00
    nellis/127315681  Makita LS1019L 10" Dual-Bevel Sliding Compound Miter Saw
```

Nothing new is collected for this. The observation database already recorded
what every lot cost each time it was looked at, and already knew when each lot
closed; `sold` is the read that puts the two together. Its answers therefore
get better on their own as more looks accumulate.

How good an answer is depends entirely on *when* the last look happened. A bid
read a minute before the close is nearly the sale price; the same bid read six
hours before says almost nothing, so readings older than `--within-minutes`
are counted and set aside rather than quoted:

```
41 closed lot(s) left out: last looked at more than 30 minute(s) before
closing, which says little about what they sold for.
```

That line is usually a schedule problem rather than a missing feature. Lots
close in a narrow band in the evening, so a run timed near the end of it turns
a whole night's inventory into closing prices at the cost of one request.

This is also why a lot carries the moment it was seen rather than the moment it
was read. A page revalidated from the cache was downloaded by an earlier run,
and a saved page can be pulled weeks later; dating either of them "now" would
turn a stale reading into an apparently fresh one.

## Reaching a whole category

When a rule finds more lots than anyone will click through, the report ends
with a way to see the same thing at the provider's end:

```
Paste into the site search to see a whole category:
  bounce house:
    splash pool | finds 3, plus 1 other lot(s)
    water slide | finds 7, plus 7 other lot(s)
    water park | finds 1, plus 3 other lot(s)
```

Several phrases rather than one, because the provider's search has no OR and a
single query cannot cover a set of unlike titles. These are chosen greedily,
cheapest first, where cheapest means the most wanted lots per unwanted lot the
same phrase surfaces -- which is the trade you actually make when you paste one
in and look at what comes back. Every phrase is one the interest rule already
asks for, so nothing here invents vocabulary you did not choose.

The counts are the point. A phrase that finds one lot and brings thirty-seven
strangers is not a shortcut, and is left out rather than offered: that lot
keeps its link. Rules matching only a handful get no phrases at all, since the
links are the shorter path.

The phrases are built from everything that matched, not from the thirty that
fitted in the report, because reaching what the cap held back is the whole
reason to offer one.

## Chat webhook

Email is the scheduled digest; it arrives whether or not anybody asked. A
webhook is the other errand -- you ran the command and want the answer on your
phone within seconds -- so it posts one message rather than a document.

```cmd
.venv\Scripts\auction-lens.exe daily --webhook
```

Turn it on with `[reports.webhook] enabled = true` and put the address in
`AUCTION_LENS_WEBHOOK_URL`. The address is a secret and is read only from the
environment, exactly as the mail password is: anyone holding it can post into
the channel, so it must never reach the configuration file or a commit.

Each lot becomes a card titled with the listing and linked to it. A provider
that publishes app links serves that same address into its own app on a phone,
so tapping a card opens the listing where you would want it and no second,
app-flavoured address is needed. The card is coloured by the provider's own
worst condition tag, and carries cost, stated retail and the share of it, the
branch, the conditions, and which rule matched.

`max_items` caps how many cards a message carries. The service accepts at most
ten embeds and rejects the whole message if given more, so ten is a ceiling
rather than a preference.

## Email reports

The command that prepares a new machine also asks for the mail settings:

```cmd
.venv\Scripts\auction-lens.exe setup --email
```

It asks for the host, SMTP username, From address, recipient, and password,
which is never echoed. The five values use the environment-variable names from
your configuration and go into the ignored `.env`, leaving its comments alone;
then it says whether
`[reports.email]` is on. It reports that rather than editing it, because
`enabled = true` is one line you own and a helper that rewrites TOML is how a
configuration quietly gets corrupted. Saving the settings is a success either
way; whether delivery is switched on is `doctor --email`'s question, and that is
the one a scheduler should ask.

Other SMTP hosts are supported when the configured port and security mode suit
the service. Gmail additionally needs 2-Step Verification and an app password
rather than the account password, and the command says so for `smtp.gmail.com`;
the full walk-through is in
[Gmail setup and delivery test](docs/GMAIL.md).

By default the CLI loads non-empty values from an ignored `.env` file in the
working directory. Existing process environment variables take precedence. Gmail
accounts normally require an app password rather than the ordinary account password.

```cmd
.venv\Scripts\auction-lens.exe run --input listings.json --config config\local.toml --email
```

Run that command from Windows Task Scheduler, cron, or another scheduler to send
a periodic digest. Successful deliveries are retained separately from
observations, so an unchanged listing is not repeated merely because two runs'
closing windows overlap. A changed bid and a relisting under a new auction id
remain eligible.

Before scheduling, check the local prerequisites without contacting the
provider or mail server:

```cmd
.venv\Scripts\auction-lens.exe doctor --email
```

`doctor` is the unattended-run gate: it also requires
`[provider.acquisition] run_mode = "production"`, so development pacing cannot
accidentally become a scheduled polling policy.

For Windows, `scripts\run-daily.cmd` is the ready-to-schedule entry point. Point
Task Scheduler at it directly: it runs the local preflight, finds today's lots,
scores and emails the findings, then emails the lots you marked `hunting` when
that selection is non-empty. It names no paths, because every path it would name
is already a default -- so moving a file is a configuration edit rather than a
script edit. Webhook delivery remains an explicit, separately configured choice.

## Getting real listings

One command asks the provider's search and writes listings ready to score:

```cmd
.venv\Scripts\auction-lens.exe discover ^
  --config config\local.toml ^
  --output data\inbox\listings.json
```

A search page carries the complete data for every lot it lists, so one request
describes a whole page of them rather than one. Terms come from `--search`, or
from `[provider.acquisition] searches`, or failing both from the `any_terms` of
your `[[interests]]` -- so what you want is written down once. In the one-door
`daily` flow, that fallback omits finite interests already satisfied by recorded
fulfillments. Explicit search lists remain explicit and are never silently pruned.

A term only finds what you can name. `[provider.acquisition] categories` sweeps
the provider's own categories as well, which is how a misspelled listing or a
thing you never thought to type still turns up. Searches and the sweep are
capped separately, so a long list of terms cannot starve the sweep.

A whole discovery run counts as a single attempt against the configured daily
limit, and the requests inside it are spaced apart. Each term's page is cached
and revalidated, so an unchanged page costs nothing.

Some providers scope their catalogue to one branch and choose it by session
rather than by URL, so `[provider.acquisition] session_url` and `session_fields`
say which branch a run is shopping. Without it the site serves its default city,
and the results look perfectly real while being hundreds of miles away.

### The closing window, and a digest in two parts

A report is a list of things you can still bid on. A lot that has already
closed never reaches scoring, however well it would have scored, because it is
no longer a bargain -- it is history.

How far the other way to reach is your choice:

```toml
[reports]
closing_within_hours = 14
```

A lot closing tomorrow evening cannot be acted on tonight, and reading about it
now only to read about it again later is how a digest stops being read. Leave
the setting out entirely to report everything still open, however distant.
Whatever the window sets aside is counted out loud, with the setting named, so
a short report is never mistaken for a quiet day.

That setting is the only built-in digest boundary. Schedule
`scripts\run-daily.cmd` twice -- say 09:00 and 17:00 -- with the same
configuration.The two 14-hour windows may overlap, but their email receipts do
not: the later run omits a still-open lot when that recipient already accepted
it at the same bid. A changed bid remains eligible, and unchanged lots are
removed before the report cap so they cannot crowd out new ones. See
[Delivery receipts](docs/DELIVERY.md) for retries and explicit resends.

The report's first line names when the earliest lot closes, because that is the
fact that decides whether the rest is worth reading now. The same fact is
offered to the subject line as `{{ first_close }}`, alongside
`{{ match_count }}`:

```toml
subject = "Auction Lens: {{ match_count }} lots, first closes {{ first_close }}"
```

Naming the close there keeps two digests on the same day from sharing a
subject, which is what makes a mail client thread one into the other.

### Near and far branches

Distance is a fact about you, not about a lot, so it is not scored. A branch you
pass anyway and one half an hour in the wrong direction are both acceptable, but
not on the same terms:

```toml
[locations]
allowed = ["phoenix", "mesa"]
far = ["phoenix"]
far_minimum_score = 85
```

Everything at a near branch is reported as usual. A lot at a far branch is
reported only if it scores at least `far_minimum_score` -- good enough to
justify the drive rather than merely good.

Setting that number needs one piece of arithmetic, because the two scoring
paths do not reach the same heights. An interest match starts at 80 and can add
at most 7 for closing within `ending_soon_minutes`, so its **quality score tops
out at 87**, and only for a lot carrying no condition penalty. Freshness is a
reading-order signal instead: a new listing can add 3 to unweighted priority,
bringing the maximum to 90, but it cannot make a lot clear a quality bar. A
retail-ratio match starts from the discount itself -- a lot at 13% of stated
retail starts at 87 -- so it clears a high bar easily.

A `far_minimum_score` of 88 or more therefore means "at far branches, show me
deep discounts but never the things I actually asked for", which is usually the
opposite of what the interest weights are for. Somewhere in the low 80s lets a
wanted thing through while still asking a discount to be remarkable.

That bar assumes the drive is a cost. Some days it is not, because you have to
be over there anyway, and on those days a far branch is simply a branch:

```cmd
.venv\Scripts\auction-lens.exe daily --visiting phoenix
```

The named branches are held to the ordinary bar for that run only. It is a flag
rather than a setting because it is true today and wrong next week, and a saved
answer to that question is one nobody remembers to change back. Repeat it for
more than one branch, and name the branch however you like -- `phoenix` and
`Phoenix, AZ` both match a `far` entry of `phoenix`.

Fetching and pulling are separate steps. `fetch` saves a provider page; `pull`
reads saved pages into the canonical file `run` analyses. Keeping them apart
means a parser can be corrected and re-run over pages already on disk without
asking the provider again.

```cmd
.venv\Scripts\auction-lens.exe pull ^
  --config config\local.toml ^
  --input private\cache\pages ^
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

Some wants end. A positive `wanted` count says how many confirmed purchases
satisfy a rule before it stops matching:

```toml
[[interests]]
name = "metal shed"
id = "yard-shed"
wanted = 1
any_terms = ["metal shed"]
```

Omit `wanted` for an ongoing interest. Auction Lens never decrements the file or
writes a hidden retired switch; it derives progress from explicit fulfillments
on won lots in the ignored watchlist. Raising `wanted`, changing a verdict away
from `won`, or clearing its allocations makes the rule active again on the next
run. Clearing also records that you reviewed the purchase and it fulfilled none,
so the report does not keep asking the same question.
If every fallback interest is satisfied and no explicit search or category sweep
is configured, `daily` makes no provider request and sends the quiet progress
report instead of failing for lack of terms.

Finite interests require a stable `id`, so improving a display name later
cannot detach it from recorded fulfillments. Ongoing interests may omit it and
use the rule name as their identity.

Each rule also has a `minimum_retail`, the floor that separates a thing from its
accessories: a guitar cable says "guitar" as loudly as a guitar does, and only
the stated value tells them apart. It pairs with `max_total_cost` -- what a lot
must be worth, and what it may cost.

### What a thing is not

The value floor stops the cheap accessories. It does not stop the expensive
ones: a set of guitar hangers outsells a beginner guitar, and a differential
carrying a power-tool brand outsells a drill. `[interest_defaults]` is where you
say what an accessory looks like, once, for every rule:

```toml
[interest_defaults]
exclude_terms = ["compatible with", "replacement", "adapter", "cable"]
accessory_nouns = ["stand", "case", "cover", "mount", "bracket", "holder"]
```

`exclude_terms` are plain phrases that mean "accessory" anywhere in a title.
`accessory_nouns` are checked only *beside* the words a rule asked for, because
the same word means opposite things at a distance -- "guitar stand" is not a
guitar, while a table saw sold "with rolling stand" is still a table saw.

One more rule needs no configuration, because it is about English rather than
about you: a title that names the wanted thing only *after* the word "for" is
describing what the lot attaches to. "Weed Wacker for DeWalt" is not a DeWalt.
"Electric Bike for Adults" still is an electric bike -- it says what it is
first, and only then who it suits.

Put only what is true of every interest in `[interest_defaults]`. Audience words
are the usual mistake: a children's guitar is a toy, but a children's water
slide is the whole point, so `kids` belongs on the guitar rule rather than in
the shared list. Anything a rule explicitly asks for is kept, so an interest in
`"monitor stand"` is never emptied by a shared `stand`.

Each rule also has a `weight`, defaulting to `1`. It decides reading order, not
eligibility: a wanted item at a fair price ranks above something you never asked
for at a steep discount. Weight is deliberately kept out of every threshold, so
`[scoring] anomaly_weight = 0.4` sinks the catch-all in the report without ever
silencing it, and raising a weight can never push a lot past a bar it failed.

Valuation sources are ordinary `[[valuation.sources]]` TOML entries. Built-in
adapters support human-reviewed XML catalogs, research-link templates, and
authorized read-only JSON APIs. Custom adapters use a normal Python import path,
so unusual integrations remain isolated. See [the valuation guide](docs/VALUATION.md)
for the configuration and XML formats.

### What the warehouse wrote on it

A title is the manufacturer's words, identical on every copy of a product. The
note is what somebody wrote after looking at this particular lot:

```
SnuggleBounce 13FT White Inflatable Bounce House
  notes: 9/8 blower not included
         leaks air/ needs a patch
```

Nothing else on the page says that, so `exclude_terms` are matched against the
note as well as the title. It is the only place a missing blower, a missing
power source, or a leaking seam is ever stated.

The note can only ever rule a lot **out**. Wanted words are still read from the
title alone, because a pallet lot's note lists everything on the pallet, and
reading wants from there would make one pallet match every interest at once. A
note is evidence against, never for.

Notes are typed into a box over several visits, so they arrive with line breaks
in them. Whitespace is flattened before matching: where somebody pressed Enter
does not decide whether a lot is reported.

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

```cmd
.venv\Scripts\auction-lens.exe logistics ^
  --source provider-id ^
  --listing-id stable-id ^
  --status feasible ^
  --added-cost 25 ^
  --note "Handling arranged"
```

Use `--status infeasible` to suppress the listing or `--status clear` to ask
again. Added logistics cost participates in configured price ceilings. The future
guided profile editor is deliberately separate; see [the roadmap](docs/ROADMAP.md).

## Watchlist

Every `run` appends one price reading -- time, bid, total cost, bid count -- for
each reported lot to an ignored `private/watchlist.json`. Scan hourly and a lot
collects an hourly trail; scan once and it collects a single point.

Alongside the trail it keeps what the provider says about the lot: the six
condition tags it grades (`Used`, `Assembly Required`, `Missing Parts` and the
rest, each red, amber, or green), its own 1-5 quality rating, and the photo
gallery -- whose last image is the photograph of the actual lot rather than the
manufacturer's stock shot.

On top of that you record what *you* think: your own estimate, a verdict, a
note, which interest a won lot actually fulfilled, and whether you reviewed
that question. A run never overwrites any of it. Match provenance and
fulfillment are separate, so one purchase never silently satisfies every rule
it happened to match.

Every report shows a copyable `Watch key` such as `nellis/synthetic-001`.
Pass that one value back with `watch --key`; the older `--source` plus
`--listing-id` spelling remains available for scripts.

```cmd
.venv\Scripts\auction-lens.exe watch ^
  --key nellis/synthetic-001 ^
  --verdict hunting ^
  --estimate 60 ^
  --note "worth it under 40 all in"

.venv\Scripts\auction-lens.exe watchlist
```

Assign a win to a finite want explicitly:

```cmd
.venv\Scripts\auction-lens.exe watch ^
  --key nellis/synthetic-001 ^
  --verdict won ^
  --fulfills soundbar
```

The next run reports `1/1 fulfilled; retired` and stops applying that interest.
A won multi-match lot accepts repeated `--fulfills`; supplying the flags
replaces the saved allocation with exactly what you name. If the purchase
fulfilled none of its matches, `--clear-fulfillments` clears any old allocation,
reopens those finite interests, and records that you reviewed the question.

The list prints keenest first, with headroom -- your estimate minus the latest
total -- so a lot that has already cost more than you said it was worth says so.
See [the watchlist guide](docs/WATCHLIST.md) for the file format and for the two
ways a condition grade is easy to read backwards.

Email only the lots you explicitly flagged as `hunting`:

```cmd
.venv\Scripts\auction-lens.exe watchlist ^
  --verdict hunting ^
  --config config\local.toml ^
  --email
```

The email is a compact set of phone-friendly cards with price, headroom,
condition concerns, the actual-lot photo, and a direct listing link. Successful
delivery is remembered per selection, so an unchanged `hunting` list does not
produce the same mail again. Add `--repeat-delivery` for an intentional resend;
see [Delivery receipts](docs/DELIVERY.md).

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

```cmd
.venv\Scripts\python.exe -m pip install -e ".[dev]"
scripts\test.cmd
```

`docs/SIMPLICITY.md` is the standard every change is measured against, and the
first thing to read before writing any: what "one door", "one authority per
fact", and "nothing bespoke" actually mean here, with the checklist to apply to
a diff. It is deliberately about the shape of a change rather than its
formatting, which ruff already settles.

`docs/ARCHITECTURE.md` is the map: which module answers which question, and the
direction dependencies are allowed to run -- a layering that is checked, not
just described. `docs/CONVENTIONS.md` is the house style: where a validation
rule belongs, when a closed set of words becomes an enum, when to split a module
and when not to, and where a new setting, scoring signal, valuation source, or
command is supposed to go.
