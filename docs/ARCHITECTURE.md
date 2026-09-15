# Architecture

The source tree is the map. Auction Lens uses feature names for directories and
plain verbs for the workflows at the top:

```
collect -> listings -> matching -> pricing -> reports
              |            |                    |
              +------ history and watchlist ----+
```

Configuration supplies policy to every stage. Providers own the authorized
boundary to an auction site. The command line names the doors into those
features, but it does not define a second version of their rules.

## Where things live

| Path | The question it answers |
|---|---|
| `collect.py` | How do authorized provider pages become one listings file? |
| `providers/http.py` | May this request run, how is it paced, and may cached data be reused? |
| `providers/nellis/discover.py` | Which public Nellis search pages should be requested? |
| `providers/nellis/parse.py` | How does a Nellis page become provider-neutral rows? |
| `providers/search_terms.py` | Which configured phrases should discovery ask for? |
| `listings/model.py` | What facts make up one provider-neutral listing? |
| `listings/files.py` | How do canonical JSON and CSV files become listings? |
| `listings/conditions.py` | What does a provider's condition answer mean? |
| `matching/analyze.py` | How does one complete analysis run flow? |
| `matching/evaluate.py` | Which gates and general bargain rules admit a listing? |
| `matching/interests.py` | How does a listing satisfy a configured interest? |
| `matching/judge.py` | Is a word match really the thing the interest describes? |
| `matching/logistics.py` | Can the item be handled with the available help and equipment? |
| `matching/progress.py` | Which finite interests remain active? |
| `matching/searches.py` | Which few provider searches cover a crowded set of matches? |
| `matching/model.py` | What is a candidate, score, and report section? |
| `pricing/value.py` | How are price sources asked and their evidence combined? |
| `pricing/sources.py` | What common contract and settings does a price source use? |
| `pricing/http_json.py` | How does a configured read-only JSON price API work? |
| `pricing/reference.py` | How does a configured reference price become evidence? |
| `pricing/xml_catalog.py` | How does a local XML price catalog become evidence? |
| `reports/records.py` | What format-neutral facts make up a report? |
| `reports/findings.py` | How do candidates become reader-facing facts? |
| `reports/text.py`, `reports/html.py` | What do those facts look like? |
| `reports/email.py` | How is a report submitted securely over SMTP? |
| `reports/webhook.py` | How does the same report fit a compact chat message? |
| `history/database.py` | Which SQLite tables and transaction boundary hold local history? |
| `history/observations.py` | What changed since a listing was last observed? |
| `history/logistics.py` | Which handling decisions has the operator recorded? |
| `history/sales.py` | What were closed lots last seen going for? |
| `watchlist/model.py` | What can a person record about one followed lot? |
| `watchlist/store.py` | How is that private, hand-readable history preserved? |
| `watchlist/report.py` | How does the followed-lot list read in text and email? |
| `config/schema.py` | What may the TOML configuration say? |
| `config/load.py`, `config/toml.py` | How is TOML read into those strict records? |
| `config/profile.py` | What do the choices mean in plain language? |
| `config/profile_edit.py` | How can the small editable profile be changed reversibly? |
| `config/profile_wizard.py` | How does a terminal walk a person through that edit? |
| `cli/parser.py` | Which commands and flags exist, and what are their defaults? |
| `cli/__init__.py` | Which command name calls which function? |

The few remaining files under `cli/` are command workflows still being moved
to their named features. `notifications.py` and `storage/deliveries.py` are the
same kind of visible migration seam: together they decide what a destination
has not received and remember successful delivery. They are deliberately named
here until that move is complete, so no contributor has to guess.

## Enforced boundaries

`scripts/check-imports.py` reads the imports from every source file and checks
the rules a maintainer actually relies on:

1. Project imports cannot form a cycle.
2. Only the console entry point may depend on `cli`.
3. Provider code cannot depend on matching, reports, local history, or command
   workflows.
4. Pure record modules cannot import I/O owners.
5. Feature-package `__init__.py` files are signposts, not hidden API barrels.

This leaves features free to keep related code together. There is no numeric
layer chart that forces one behavior to be scattered across unrelated folders.

## Rules that keep it navigable

1. **One question per module.** Split when parts change for different reasons,
   not merely because a line count is large.
2. **Explicit imports.** Import from the file that owns a name. Do not make a
   package marker into a second, hidden directory of re-exports.
3. **Gates before scores.** A rejected listing leaves before arithmetic runs,
   so its rejection stays cheap to explain.
4. **Records enforce their own rules.** Downstream code trusts the values it
   receives instead of repeating validation.
5. **Names say what; comments say why.** A reader should understand the normal
   path without comments, and understand the non-obvious tradeoff from them.
6. **Price sources are data first.** Add an authorized source in TOML when the
   existing adapters can express it; add code only for a new input mechanism.

## Reading the code for the first time

Start with `matching/analyze.py`. It shows the provider-neutral path from
listings through observation, matching, optional judging, pricing, ranking, and
watchlist history. Follow a call into the feature whose decision interests you.

For the network boundary, start at `collect.py` and then open
`providers/nellis/discover.py`. For what a person receives, start at
`reports/findings.py`. Read `cli/parser.py` when you need the public command
surface rather than the domain rules.

## Tests

`tests/` is organized by behavior, with shared synthetic fixtures and fakes in
`tests/support.py`. The `tests/contracts/` directory pins the public CLI and
cross-channel report meaning during structural refactors. Compatibility
fixtures protect old watchlist and SQLite files. Nothing in the suite contacts
a provider, SMTP server, or webhook.

Run everything CI runs with `scripts\test.cmd` on Windows or
`python scripts/check.py` elsewhere.
