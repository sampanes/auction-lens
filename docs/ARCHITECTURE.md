# Architecture

Auction Lens is a pipeline with a strict direction of dependency. Data moves
left to right, and no module imports anything to its left.

```
acquisition -> ingest -> scoring -> valuation -> reporting
                  \                              /
                   \--------- storage ----------/
```

`fields` has no project dependencies. `models` depends only on `fields`; both
sit underneath the rest of the application.

This is not a description. It is checked on every run by
`scripts/check-imports.py`, which holds the layers below as data:

| Layer | Modules | May import |
|---|---|---|
| 0 | `fields` | nothing in the project |
| 1 | `env_file`, `file_io`, `models` | layer 0 |
| 2 | `config` | layers 0-1 |
| 3 | `logistics` | layers 0-2 |
| 4 | `acquisition`, `ingest`, `reporting`, `scoring`, `storage`, `valuation` | layers 0-3 |
| 5 | `pipeline` | layers 0-4 |
| 6 | `cli` | everything |

Modules on the same line are peers and may not import each other. That is what
keeps `scoring` readable without `valuation` open beside it.

## Where things live

| Module | Answers |
|---|---|
| `fields` | What is this value allowed to be? |
| `models` | What is a listing, a candidate, a valuation? |
| `config/` | What did the operator's TOML file ask for? |
| `ingest` | How do canonical JSON and CSV files become listings? |
| `acquisition/` | May we contact the provider right now, and what did it say? |
| `scoring/` | Is this listing worth reporting, and why? |
| `logistics` | Is getting this item home still an open question? |
| `valuation/` | What is it actually worth, according to whom? |
| `storage/` | What did we see last time? |
| `reporting/findings` | What does the report say? |
| `reporting/text`, `reporting/html` | What does that look like? |
| `reporting/delivery` | How does it get sent? |
| `pipeline` | One whole run, without a command line. |
| `cli/` | Which arguments map to which command, and how are errors shown? |
| `file_io`, `env_file` | Shared plumbing with no domain opinions. |

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
server, or a real provider. Run everything CI runs with `scripts\test.cmd`.
