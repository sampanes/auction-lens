"""Whether a sweep is owed, and whether there is anything fresh to mail.

Two unrelated readers need answers here and must not disagree. The companion
asks whether a sweep is owed, so it knows whether to start one. The morning
mail runner asks whether there is anything fresh to say, so it can stay silent
rather than send a digest of nothing new.

They are deliberately different questions, asked at opposite ends of the night,
and they are answered from different evidence. Getting either one wrong has
silenced the feature completely once already, so both reasons are written down.

The companion's question is a calendar one: a sweep becomes owed at local
midnight and runs the first time Chrome is open after that, so a machine that
was switched off at whatever minute was chosen still gets its sweep rather than
losing the day. Its evidence is the note the companion writes when a sweep
finishes.

The mail's question is about age, not the calendar. Asked whether a sweep
happened "today", the gate rejected a sweep from seven hours earlier and sent
nothing, every morning: the run log recorded "no sweep today, nothing sent" at
07:00 on a day whose sweep had finished the previous evening.

And its evidence is the records, not that note. Asking whether a sweep
*finished* silenced the mail a second time. A sweep is not reliably allowed to
finish: Amazon throttles it after a term or two, and it abandons the attempt
rather than keep knocking. Those truncated attempts still wrote 278 rows across
fifteen searches in one night, and the gate still sent nothing, because no
sweep had reported itself done. A sweep finishing is not what the reader cares
about. Something new arriving is, and the records are where that is recorded:
the sink stamps every row with the moment it was appended, so the newest of
those stamps is exactly "when did anything last arrive". A pipeline that is
genuinely dead has no recent rows and still mails nothing, which is the
behaviour that was wanted.

The date is local because "has today's sweep happened" is a question about the
operator's day. Timestamps are UTC because an elapsed time must not change when
the clocks do, and record filenames are UTC because they are storage.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from typing import Any

# How old the newest record may be and still be worth mailing. Wide enough that
# an evening's collecting covers the next morning's run with hours to spare, and
# narrow enough that a day with Chrome closed throughout is not reported as
# news.
MAIL_WINDOW_HOURS = 18

# A row stamped slightly ahead of the reader's clock is ordinary skew between
# two processes, not evidence of anything. One stamped further ahead than this
# is a clock that cannot be reasoned about, and the gate declines rather than
# mail on arithmetic it does not trust.
CLOCK_TOLERANCE = timedelta(hours=1)


def today() -> str:
    """The operator's own date, which is what "today's sweep" means."""
    return date.today().isoformat()


def now_utc() -> datetime:
    """The instant freshness is measured against."""
    return datetime.now(UTC)


def moment_from(value: Any) -> datetime | None:
    """One stored timestamp as an instant, or None when it cannot be read.

    Anything unreadable is None rather than raised on. A scheduled morning run
    must not die on one corrupt line, and a line it cannot read is a line it
    has no business counting as news.

    A timestamp without a zone is read as UTC, because that is what every
    writer here records and the alternative is discarding a readable instant.
    """
    if not isinstance(value, str):
        return None
    # A bare date is not an instant: it could mean any of twenty-four hours,
    # and `fromisoformat` answers midnight without saying it guessed. Read as
    # an arrival time, that midnight would look fresh for most of the next
    # day and let the gate mail on nothing. Every writer here records a full
    # timestamp, so one without a time of day is declined like any other
    # value this cannot trust.
    if "T" not in value and " " not in value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def newest_moment(values: Iterable[Any]) -> datetime | None:
    """The latest readable instant among stored timestamps, or None if none is.

    Unreadable values are skipped rather than failing the whole stream: one bad
    line in a day's records should not hide the rest of the day.
    """
    newest: datetime | None = None
    for value in values:
        moment = moment_from(value)
        if moment is not None and (newest is None or moment > newest):
            newest = moment
    return newest


def is_recent(
    moment: datetime | None, now: datetime, hours: int = MAIL_WINDOW_HOURS
) -> bool:
    """Whether an instant is inside the window. The mail runner's question.

    Fails closed on every shape it cannot trust: nothing to read, an instant
    older than the window, and one so far ahead of the reader's clock that the
    subtraction means nothing.
    """
    if moment is None:
        return False
    age = now - moment
    if age < -CLOCK_TOLERANCE:
        return False
    return age <= timedelta(hours=hours)


def describe_age(moment: datetime | None, now: datetime) -> str:
    """How long ago an instant was, for a line an operator will read."""
    if moment is None:
        return "never"
    hours = (now - moment).total_seconds() / 3600
    if hours < 0:
        return "just now"
    if hours < 1:
        return f"{int(hours * 60)} minutes ago"
    return f"{hours:.1f} hours ago"


def last_sweep_date(stamp: Any) -> str:
    """The date in the companion's note, or empty when there is nothing to read.

    Anything unreadable is treated as "never swept" rather than raised on. The
    cost of being wrong is one extra sweep, and the alternative is a scheduled
    run that dies on a corrupt one-line file.
    """
    if not isinstance(stamp, dict):
        return ""
    value = stamp.get("date")
    return value if isinstance(value, str) else ""


def sweep_is_due(stamp: Any, now: str) -> bool:
    """True until a sweep has finished today. The companion's question.

    A date from the future is still "not today", so a clock that went backwards
    asks for one more sweep instead of never asking again.
    """
    return last_sweep_date(stamp) != now
