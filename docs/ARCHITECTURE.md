# Architecture

Auction Lens is a pipeline with a strict direction of dependency. Data moves
left to right, and no module imports anything to its left.

```
acquisition -> ingest -> scoring -> valuation -> reporting
                  \                              /
                   \--------- storage ----------/
```

`fields` has no project dependencies. `grading` depends only on `fields`, and
`models` on both; all three sit underneath the rest of the application.

This is not a description. It is checked on every run by
`scripts/check-imports.py`, which holds the layers below as data:

| Layer | Modules | May import |
|---|---|---|
| 0 | `fields` | nothing in the project |
| 1 | `grading` | layer 0 |
| 2 | `env_file`, `file_io`, `http_safety`, `models`, `text_match`, `throttle` | layers 0-1 |
| 3 | `config` | layers 0-2 |
| 4 | `logistics`, `notifications`, `outcomes` | layers 0-3 |
| 5 | `acquisition`, `ingest`, `reporting`, `scoring`, `storage`, `valuation` | layers 0-4 |
| 6 | `pipeline` | layers 0-5 |
| 7 | `cli` | everything |

Modules on the same line are peers and may not import each other. That is what
keeps `scoring` readable without `valuation` open beside it.

## Where things live

| Module | Answers |
|---|---|
| `fields` | What is this value allowed to be? |
| `grading` | What does a provider's condition answer mean, and what colour is it? |
| `models/lots` | What is a listing, and what is one called? |
| `models/scale` | What is a score out of, and what can a want reach of it? |
| `models/candidates` | What is a lot plus the reason it is reported? |
| `models/watching` | What does the operator think of a lot, and what has it cost? |
| `models/interests`, `models/handling`, `models/valuation` | Which want, can it be carried, what is it worth? |
| `config/` | What did the operator's TOML file ask for? |
| `config/profile` | What do those stable operator choices mean in plain language? |
| `config/editor` | How can a small profile change preserve the original TOML and be reversed? |
| `ingest/canonical` | How do canonical JSON and CSV files become listings? |
| `ingest/nellis` | How does one saved provider page become a canonical row? |
| `ingest/turbo_stream` | How is a streamed page payload decoded? |
| `acquisition/` | May we contact the provider right now, and what did it say? |
| `acquisition/discover` | Which lots exist, asked once per search term? |
| `scoring/` | Is this listing worth reporting, and why? |
| `logistics` | Is getting this item home still an open question? |
| `notifications` | What has this destination not successfully received yet? |
| `outcomes` | Which finite interests remain active, and which wins still need fulfillment review? |
| `valuation/` | What is it actually worth, according to whom? |
| `valuation/settings` | What may one source's adapter settings say? |
| `storage/` | What did we see last time? |
| `storage/deliveries` | What did each report destination successfully receive? |
| `storage/watchlist` | Which lots am I following, and what have they cost? |
| `storage/sales` | What were closed lots last going for before they closed? |
| `reporting/findings` | What does the report say? |
| `reporting/text`, `reporting/html` | What does that look like? |
| `reporting/watchlist` | What does the followed list look like? |
| `reporting/sales` | What do closing prices look like, and how far can they be trusted? |
| `reporting/delivery` | How does it get sent? |
| `pipeline` | One whole run, without a command line. |
| `cli/parser` | Which words and flags exist, and what are their defaults? |
| `cli/setup`, `cli/prompts` | How does a fresh clone become a working one? |
| `cli/collect` | How do lots get from a provider into a file? |
| `cli/analyze` | How does a file of lots become a report someone reads? |
| `cli/track` | What does the operator think of one particular lot? |
| `cli/doctor` | Would a scheduled run work right now? |
| `cli/sending`, `cli/searching`, `cli/exit_codes` | The answers more than one command needs. |
| `file_io`, `env_file`, `http_safety`, `throttle` | Shared plumbing with no domain opinions. |

## Rules that keep it navigable

1. **One reason to change per module.** Keep helpers beside the behavior they
   explain. Split a file when its parts change for different reasons, not merely
   because it can be made smaller.
2. **Take the narrowest configuration you need.** Cost estimation takes
   `EconomicsConfig`, not the whole `AppConfig`; only `pipeline` and `cli` see
   everything.
3. **Gates before scores.** A listing that is rejected is rejected before any
   arithmetic runs, so a rejection is cheap to explain.
4. **A record enforces its own rules.** Nothing downstream re-checks a value
   that a record already guarantees.
5. **Names over comments.** A comment should say *why*; the code says *what*.
6. **Sources are data.** Adding a marketplace is a TOML edit. Adding an
   *input mechanism* is a new adapter behind the `ValuationAdapter` protocol.

`CONVENTIONS.md` says what these look like in practice, and what to do when
adding something.

## Reading the code for the first time

Start with `pipeline.analyze_listings`, which shows one complete run in about a
page. Follow `evaluate` into `scoring/` for selection policy, or follow the
stores into `storage/` for persistence. Read `cli/` last: it deliberately adds
argument names and terminal output, but no domain behavior.

## Tests

`tests/` mirrors the module layout, one file per area, with shared fixtures and
fakes in `tests/support.py`. Nothing in the suite touches the network, an SMTP
server, or a real provider. Run everything CI runs with `scripts\test.cmd` on
Windows or `python scripts/check.py` elsewhere.
