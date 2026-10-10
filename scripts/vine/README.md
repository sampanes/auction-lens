# Vine companion

Amazon Vine has no API and its item queues sit behind a customer login, so the
only place those pages can be read is the operator's own signed-in browser.
This directory holds the two halves of that arrangement:

- `extension/` -- an unpacked Chrome extension that reads the item grid already
  rendered in front of the operator and reports it.
- `sink.py` -- a loopback HTTP sink that tells the extension which terms to look
  for and appends what it saw to ignored `private/vine/`.

Nothing in the Python package talks to Amazon. The extension makes the same
ordinary page requests a person makes, in the session that person is already
signed into, and it never requests a Vine item.

## Why a sink instead of settings in the extension

The search terms already have an owner: `providers/search_terms.py`, reading
the configured searches or the interest rules. If the extension carried its own
list, the same fact would live in two places and drift. It carries none, and
asks for a plan instead.

Pacing is the sink's own choice rather than the provider block's. That block
records a cadence an operator negotiated with an auction provider for that
provider; reusing it here would quietly claim an authorization nobody gave.
Change it with `--spacing`, `--max-searches` and `--max-pages`.

The port is the one fact the two halves each hold a copy of: `--port` here and
`SINK` in `sweep.js`. Changing it means editing both. The extension asks for
`http://127.0.0.1/*` rather than one port so that edit does not also need a
manifest change.

## Running it

    scripts\vine\start-sink.cmd

It requires `config\vine.toml` and refuses without one, rather than falling
back to the sink's own default. That default is the auction provider's config,
whose search terms were written for an auction warehouse: serving those to
Amazon's search box would run a whole sweep, report success, and have searched
for the wrong things. Start from `config\providers\vine.example.toml`.

It also refuses a config that declares no `searches`. Both refusals are the
same shape -- a sweep that runs and reports success having searched the wrong
list is worse than one that will not start.

The window is titled `auction-lens-vine-sink`. Starting it twice replaces the
first one rather than leaving two fighting over port 8787.

    tasklist /FI "WINDOWTITLE eq auction-lens-vine-sink*"
    netstat -ano | findstr :8787
    scripts\vine\stop-sink.cmd

Load the extension once, in Chrome: `chrome://extensions`, enable Developer
mode, "Load unpacked", choose `scripts\vine\extension`.

## The three modes

**Passive, and the default.** Any Vine item page the operator opens themselves
is read and recorded. If the sink is not running the extension records nothing
and says so in its console.

**A sweep, which the operator starts.** With a Vine item page open in the active
tab, click the toolbar button. The extension asks the sink for the plan, then
visits one search per term at the configured spacing, recording each page. The
badge counts the terms remaining. Clicking again stops it. It also stops when
the terms run out or the search budget is spent.

**One sweep a day, which nobody starts.** Every half hour, while Chrome happens
to be running, the extension asks the sink whether a sweep is owed. One is owed
until a sweep has finished today. When it is, the extension opens its own
background tab, sweeps the window of terms, closes the tab again, and tells the
sink it is done. Nothing of the operator's is touched: their tabs are left as
they were, and a tab is only ever closed if this opened it.

The due question is a different route from the plan on purpose. Asking costs
nothing and happens constantly; `/plan` moves the rota on, so answering both
from one route would walk the whole term list in an afternoon of polling and
search none of it.

It is a local date, not a time of day. A sweep becomes owed at midnight and
runs the first time Chrome is open after that, so a machine that was asleep at
whatever hour was chosen still gets its sweep rather than losing the day.

The morning mail asks a different question, and the two must not be merged.
This one is a calendar question about whether a sweep is owed. That one is
about how old the last sweep is, because a sweep happens in the evening and
the mail goes at seven -- so the freshest records that exist are always
stamped yesterday. A day with Chrome closed throughout still mails nothing.

A term deeper than `--max-pages` is followed only that far. Every page visited
spends the same budget a term does, which is what keeps one productive term
from turning a twelve-search sweep into fifty.

Hovering the toolbar icon reports what last happened -- how many items were
recorded and when, or why nothing was. While browsing, the badge shows the size
of the last batch; while sweeping, it shows terms remaining.

On a throttle -- no item grid, which is what a 429, an interstitial or a
sign-in wall looks like from inside the page -- the sweep waits 30s, 1m, 2m, 4m,
8m, then 15m, retrying the same term, and abandons the sweep rather than keep
knocking.

## What drives a sweep, and why silence used to end one for days

Nothing in `sweep.js` loops. A sweep advances because the page it opened sent
a message, so every page it visits has to send exactly one, and the two halves
have to agree on what counts as an answer.

A search with no results is an answer. Amazon renders the grid and then never
touches it again, and `harvest.js` used to return silently on an empty one: no
tiles, no "blocked", nothing. The sweep then sat waiting for a message that
was never coming. Because a sweep is only started when none is already
running, that one fruitless term also declined every later sweep, until the
browser or the extension was restarted -- and `/plan` had already moved the
rota past the whole window. Observed across several evenings: two terms of
twelve recorded, no finish reported, thirteen of forty-six terms ever searched.

Three rules now hold that shut, and the first is the fix rather than the
backstop:

- An empty grid reports itself, as a reading with no items. It costs a term
  and no backoff, because the term was searched and the queue held nothing.
  A missing grid still reports "blocked" and still earns the ladder above:
  those are different facts and the sweep spends them differently.
- A page answers once, and the sweep only acts on an answer that names what
  it asked for. Amazon rewrites a grid in place rather than reloading it, so
  one page can report twice; by the time the second report arrives the sweep
  has already navigated, so counting visits cannot tell it apart from the
  next page's first report. Acting on both skips a term without searching it,
  which afterwards reads exactly like a term that found nothing. Each report
  states the search and page number it read, and that is what decides it.
  On the page side the empty answer is also final, so an empty grid that
  fills a moment later does not speak twice.
- A sweep carries a deadline for the answer it is waiting on, and the alarm
  that asks whether a sweep is owed ends one that has gone quiet past it. It
  reports what it did search, closes the tab it opened, and lets the next
  check start again; a sweep that searched nothing claims nothing, so the
  evening is retried rather than lost.

Those three are tested, in `scripts/vine/test-extension.js`:

    node scripts/vine/test-extension.js

`scripts/check.py` runs it as its last step and refuses without Node rather
than skipping it, because a gate that reports success over an unchecked half
is worse than one that will not start. The test loads both files into stubbed
page and worker contexts and drives a whole sweep through them, which is the
only way to ask "does a term with no results reach the next term" without
Amazon. It needs Node and nothing else: there is no package.json and no
lockfile here, so no published package can change what that check does.

Its fixtures are invented, like every other fixture in this repository. A
test that named a real search term or a recorded product would put the
operator's own criteria into public history.

What it covers is control flow: which message each half sends, and where the
sweep goes next. It does not know Amazon's markup, so a renamed grid selector
would pass it and break the companion. That failure looks different and is
the one the hover text and the page console are for.

## Where the logs are

Two different consoles, which is the usual reason a message cannot be found:

- `harvest.js` logs to the Vine page's own console (F12 on that tab).
- `sweep.js` logs to the service worker console, reached from the "service
  worker" link on the extension's card in `chrome://extensions`.

Editing anything under `extension/` needs a **Reload** on that card before the
change takes effect.

## What a record holds

Three files live in `private/vine/`: the day's records, `sweep-offset.json`
holding the rota's position, and `last-sweep.json` holding the local date a
sweep last finished. That last one answers "has today's sweep happened", which
is the companion's own question and nobody else's -- and it only exists on a
night when a sweep was actually allowed to finish.

The morning mail deliberately does not read it. Its question is whether
anything arrived lately, and the records answer that themselves: `received_at`
is the moment the sink appended a row. Reading the note instead silenced the
mail on every night a sweep was throttled part-way, which is most of them.

One JSON object per line in `private/vine/queue-YYYY-MM-DD.ndjson`:

    source, asin, title, image_url, queue, search_term,
    category_node, page, pages_available, seen_at, etv, received_at

`pages_available` is the highest page the pager offered. Amazon shows a window
of page numbers rather than all of them, so on a long result set it is a floor
on what exists rather than a count of it.

A title is folded to ASCII when `pull` reads it, not when it is recorded,
so the record keeps what the page said and every reader downstream gets a
string it can print. These sellers write worldwide: fullwidth commas, en
dashes, non-breaking hyphens, multiplication signs. A report is text, and one
character outside the console encoding ended the entire run with a charmap
error and no report at all. The substitutions are the point rather than the
stripping -- a picture frame advertised as 24" states its size with a right
quotation mark, and that is the character a rule about a dimension is looking
for. `providers/vine/read.py` owns the table.

`etv` is always null. The grid does not carry an estimated taxable value; that
number appears only in the per-item details modal, so reading it costs one
request per item. The cheap filter therefore runs first on titles, and only a
lot that already matched is worth spending a request on. Recording the null
keeps a later enrichment step honest about which rows it has answered.

## Two lists, not one

The most expensive mistake available here is treating these as the same list:

- `[provider.acquisition] searches` is the **recall net**: the words typed into
  Vine's own search box to pull candidates out of a catalogue of roughly 48,000.
  Broad and few, because a search costs a request.
- An interest rule's `any_terms`, `none_terms` and `wants` are the
  **discrimination**: what decides whether a candidate is the thing. They are
  never searched for.

The sink refuses to start without `searches`, rather than falling back to
flattening every rule's `any_terms`. That fallback is right for an auction,
where a rule's terms are its searches, and wrong here: a Vine rule pins its
host product in `all_terms`, which the fallback never reads, so the sweep would
search the dozens of fittings and never the product line itself, all evening,
and report success.

A search trick belongs in `searches`, where it is aimed at a search engine: a
term like `cordless drill ah` makes Amazon surface titles that happen to state
a battery capacity. A specification belongs in `wants`, which is prose a judge
weighs. Neither belongs in `none_terms`, which is literal and will quietly
delete a lot whose title happens to contain the word.

## The rota

`max_searches` is how many searches one sweep may spend, which is not the same
claim as how many terms exist. A list longer than the budget is served a window
at a time and the sink remembers where it stopped, so consecutive sweeps cover
different terms and the list eventually comes round. The console says which
part it is serving:

    [sink] serving terms 13-24 of 30
    [sink] serving terms 25-30 and 1-6 of 30

The second line is a window that wrapped. It is reported as two runs because
reported as one it reads "terms 25-36 of 30", which invites the reader to
distrust the count rather than the sentence.

The offset lives in `private/vine/sweep-offset.json`. Delete it to start the
rota over. Without this the plan was a prefix rather than a rota: the first
twelve terms were searched every evening and the rest never once. The
arithmetic is in `providers/vine/rota.py` and is tested; this file owns only
where the number is kept.

## Reading the records into the engine

The records are the handoff. From here it is an ordinary provider, except that
this one cannot fetch:

    copy config\providers\vine.example.toml config\vine.toml
    .venv\Scripts\python.exe -m auction_lens pull --config config\vine.toml ^
        --input private\vine --output private\vine-listings.json
    .venv\Scripts\python.exe -m auction_lens run --config config\vine.toml ^
        --input private\vine-listings.json

`pull` reads `*.ndjson` here rather than saved HTML, because the adapter names
its own file shape. Every sighting in a day's file is kept on disk; `pull`
collapses them to one lot per ASIN, keeping the earliest.

## The morning mail

`scripts\vine\run-vine.cmd` is the unattended version of those two commands,
and `scripts\schedule.cmd` owns the time it runs at, as it does for every other
run. It is the back half only: nothing in it asks Amazon for anything.

    doctor --email        refuse now if the mail or the judge is not ready
    fresh-records.py      exit 1 if nothing arrived inside the 18h window
    pull                  collapse the day's sightings into lots
    run --email           score them and send what matters

It sends nothing when no record has arrived in the last 18 hours, rather than
mailing a digest of nothing new, and logs the skip to ignored
`private/vine-run.log` -- because silence with no trace is how a dead pipeline
goes unnoticed for weeks.

The gate asks about records rather than sweeps because a sweep finishing is not
what the reader cares about. Amazon throttles a sweep after a term or two and
it abandons the attempt rather than keep knocking, so a night can collect
hundreds of rows across a dozen searches without one sweep ever reporting
itself done. Both earlier versions of this gate asked a question the pipeline
could not answer, and both sent nothing for days.

That window is hours rather than a calendar date, and the first version got it
wrong in a way worth remembering. It asked whether a sweep had happened today.
A sweep runs in the evening and this runs at seven the next morning, so the
answer was always no: the log read `no sweep today, nothing sent` at 07:00 on a
morning whose sweep had finished seven hours earlier, and the feature never
sent anything at all. `MAIL_WINDOW_HOURS` in `providers/vine/swept.py` owns the
number, and that module explains why the companion's question and this one are
deliberately different.

That gate is an exit code rather than a captured string, and the comment in the
runner says why: the first version captured two values inside a `for /f`, got
the nested quoting wrong, both came out empty, empty compared equal to empty,
and a run went out on the one morning the gate existed to stop. Every failure
here -- unreadable note, missing file, broken interpreter -- now lands on a
non-zero code, which is the direction a gate should fail in.

`doctor` is the only preflight an unattended run gets, so it reports this
provider's inability to fetch as a fact and goes on to check the mail settings
and the judge. Those are what such a run actually fails on at seven in the
morning. The refusal still stands everywhere it means something; the registry
states which providers fetch, and nothing in that branch makes this one able to.

`discover`, `daily` and `doctor` all refuse under this provider and exit 2.
That is the construction, not a missing feature: both discovery callables in
`src/auction_lens/providers/vine/read.py` raise, so no configuration and no
scheduled task can make the Python side request a Vine page. Only the
companion reads Amazon, only in a session a person signed into.

Set `income_tax_rate` in the config before trusting a cost. A Vine item has no
bid, so with the rate left at zero the engine is told every item is free -- and
an interest's `max_total_cost` cannot decline a lot that costs nothing. With
the rate set, a lot's cost is the rate times its declared value.

For the same reason these lots never appear as price anomalies. With no bid the
cost is the rate times the value, so the discount is the rate for every item in
the catalogue alike -- identical, and therefore no evidence about any of them.
A Vine lot reaches a report because an interest asked for it.

Until ETV is read, `etv` is null on every row, so `estimated_retail` is unset
and no Vine lot has a cost at all. Terms and conditions still match; money does
not. That is the next thing worth building.

## Observed page contract

Checked 2026-10-06 against a live signed-in queue:

| Thing | Shape |
|---|---|
| Queues | `?queue=potluck` recommended, `queue=last_chance` available for all, `queue=encore` additional items |
| Term search | `&search=TERM`, a plain GET; the box repopulates from the URL |
| Pages | `&page=N`, 36 tiles per page |
| Category | `&pn=NODE` |
| A tile | ASIN in a `/dp/` link, full title in the offscreen half of the truncation widget, no ETV |

Search returned identical results for the same term under `encore` and
`last_chance`, so the sweep names one queue and treats it as the catalog. If
that stops holding, `SWEEP_QUEUE` in `sweep.js` is the line that grows.

## Fixtures

Any test or fixture for this gets written synthetically. A real Vine page
carries the operator's own delivery identity in its header, and recorded rows
are account-linked; none of that belongs in tracked files.
