# Architecture

Auction Lens is a pipeline with a strict direction of dependency. Data moves
left to right, and no module imports anything to its left.

```
acquisition -> ingest -> scoring -> valuation -> reporting
                  \                              /
                   \--------- storage ----------/
```

`models` and `fields` sit underneath all of it and depend on nothing.

## Where things live

| Module | Answers |
|---|---|
| `fields` | What does this loosely typed value mean? |
| `models` | What is a listing, a candidate, a valuation? |
| `config/` | What did the operator's TOML file ask for? |
| `ingest` | How do canonical JSON and CSV files become listings? |
| `acquisition/` | May we contact the provider right now, and what did it say? |
| `scoring/` | Is this listing worth reporting, and why? |
| `logistics` | Is getting this item home still an open question? |
| `valuation/` | What is it actually worth, according to whom? |
| `storage/` | What did we see last time? |
| `reporting/` | How is this said to a person, and how is it delivered? |
| `pipeline` | One whole run, without a command line. |
| `cli/` | Which arguments map to which command? |
| `file_io`, `env_file` | Shared plumbing with no domain opinions. |

## Rules that keep it navigable

1. **One concern per module.** If a file needs the word "and" to describe it,
   it is two files.
2. **Take the narrowest configuration you need.** Cost estimation takes
   `EconomicsConfig`, not the whole `AppConfig`; only `pipeline` and `cli` see
   everything.
3. **Gates before scores.** A listing that is rejected is rejected before any
   arithmetic runs, so a rejection is cheap to explain.
4. **Names over comments.** A comment should say *why*; the code says *what*.
   Numbers that encode a judgement (`ENDING_SOON_BONUS`, `SAMPLE_SIZE_CAP`)
   are named constants, not literals in an expression.
5. **Sources are data.** Adding a marketplace is a TOML edit. Adding an
   *input mechanism* is a new adapter behind the `ValuationAdapter` protocol.

## Adding something

- **A new setting**: add the field to the record in `config/schema.py` and read
  it in the matching builder in `config/loader.py`. Nothing else changes.
- **A new scoring signal**: add a named constant and function to
  `scoring/signals.py`, then use it where it applies. `ScoringContext` carries
  anything both interests and anomaly discovery need.
- **A new valuation source**: prefer configuration. If the source genuinely
  needs code, implement `collect(listing)` and register it in
  `valuation/registry.py`, or point `adapter` at an import path.
- **A new command**: add the parser in `cli/parser.py`, the function in
  `cli/commands.py`, and the mapping in `cli/__init__.py`. Anything with real
  logic belongs in `pipeline` instead, where it can be tested without argv.

## Tests

`tests/` mirrors the module layout, one file per area, with shared fixtures and
fakes in `tests/support.py`. Nothing in the suite touches the network, an SMTP
server, or a real provider.
