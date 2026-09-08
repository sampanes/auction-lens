# What "simple" means here

This project keeps being handed to different people and different assistants.
Each one is competent, and each one leaves it slightly more complicated than
they found it -- never unreasonably, always for a defensible reason. This file
exists so the standard is written down once instead of re-argued every time.

It is not a style guide. Formatting is settled by ruff. This is about the
shape of a change.

## The one sentence

**Nothing should look bespoke; everything should look intelligently simple.**

Someone who has never seen this code should be able to open any file, read it
top to bottom, and understand both what it does and why it is that way. If a
reader has to hold three files in their head to follow one idea, the idea is in
the wrong place.

## The principles

### 1. One door

There is one obvious way to do a thing. `daily` is the day's work. `setup` is
first-run. `config/local.toml` is what you edit. A second entry point that does
the same job is not a convenience, it is a thing that will drift out of step
with the first one and quietly become wrong.

> A scheduler script was added alongside an existing one, in a second language,
> with the same argument list. Both were correct on the day they were written.
> The question is what happens six months later when only one gets updated.

### 2. One authority per fact

Every fact lives in exactly one place. Where the report order is decided. What
counts as "far". Which words a person reads in a listing. What a valid mail
setup looks like. If two places know the same fact, they can disagree, and the
bug that follows is invisible until it matters.

> `.env` parsing existed in Python. A setup helper reimplemented it in
> PowerShell. Neither was wrong; together they were two answers to one question.

### 3. Do not restate a default

If the config already says it, do not repeat it in a script. If the CLI already
defaults to it, do not pass it. A repeated default turns one edit into two, and
the second one gets forgotten.

> A scheduler passed six paths explicitly. All six were already the defaults.
> Moving a file would have needed two edits, and the script would have won.

### 4. Configuration over code, for anything about the operator

Facts about the person -- what they want, where they will drive, whether they
own a trailer -- change. Those belong in configuration, named plainly, so
changing your mind is one line. Do not encode them as filters scattered across
rules, and do not hard-code them at all.

> "I have no trailer" became `large_item_policy`, one setting, rather than an
> exclusion rule repeated on every interest. The day the trailer arrives, one
> word changes.

### 5. Rank, do not silently exclude

Prefer showing something ranked lower to hiding it. When something *is* hidden,
say so out loud. A short report and a quiet day must never look identical.

> The report cap prints "17 more matched" rather than simply showing five.

### 6. Say why in the code, not just what

A comment that repeats the code is noise. A comment that records *why* a
decision was made -- especially a decision that looks wrong at first glance --
is the most valuable thing in the file, because it stops the next person
"fixing" it.

> The condition tag colours mirror the provider and look miscalibrated. The
> module docstring says so, says it was changed once, and says why it was
> changed back.

### 7. Small pieces with honest names

Small functions that do one thing, named for what they do. A function's
signature is a promise: every parameter must be used, every name must be true.

> A mail function kept accepting a `path` argument it had stopped using.
> Callers passed it. It went nowhere. The behaviour was right and the signature
> lied, which is worse than either problem alone.

### 8. Prefer the standard library and the boring solution

Zero runtime dependencies is a feature. So is choosing the obvious approach over
the clever one. If a solution needs a paragraph to justify, it probably needs a
different solution.

## What this does *not* mean

Simplicity is not the same as less code, and this is where it usually goes
wrong in the other direction:

- **Not fewer files.** Splitting one long function into four named ones is
  simpler even though it is more lines. Modularity where modularity is merited.
- **Not fewer tests.** Tests are how a reader learns what the code guarantees.
  A test named for the behaviour it protects is documentation that cannot rot.
- **Not skipping validation.** Failing early with a clear message is simpler
  than a confusing failure later.
- **Not removing care that is earned.** Hiding a password while it is typed is
  not complexity; it is the minimum. The question is never "is this extra work"
  but "does the risk it defends against actually exist here".

## The test to apply to a change

Ask these in order. Any "no" is worth a second look, not an automatic veto.

1. Could someone unfamiliar read this and understand why, not just what?
2. Is this the only place that knows this fact?
3. Does it add a second way to do something that already had a way?
4. Does it repeat a value that is already a default or already in config?
5. Is anything hidden from the operator without being counted out loud?
6. Does every parameter get used, and does every name tell the truth?
7. Is the complexity proportionate to a risk that is real *for this project* --
   a personal, read-only tool reading a config its own owner wrote?

Question 7 is the one most often missed. Defending against an attacker who can
already edit your configuration file is not caution, it is cost with no
benefit. Defending against a redirect to a host you never authorized is real,
because the request carries your contact address. Same technique, different
answer, and the difference is the threat, not the code.

## When in doubt

Leave the simpler thing and write down what you were worried about. A comment
naming a risk costs nothing and can be acted on later. Code defending against a
risk nobody has articulated is permanent, and the next reader will not know
whether they are allowed to remove it.
