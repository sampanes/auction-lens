"""Reading one closed lot's own page, which is where a hammer price exists.

This is the expensive half of the provider and the only part that asks for a
lot at a time. ``discover.py`` says why that is normally wrong: a search page
describes forty lots for one request, so asking per lot is forty requests for
data already received. None of that applies after a close. A closed lot is not
in the search pages at all, and its result is not in any page that lists it, so
there is no cheaper question to ask.

Because it is expensive, it is gated separately. Permission to poll public
search pages is not permission to walk a page per lot, and the operator
confirmed theirs from provider correspondence, so ``settle_authorized`` is its
own flag and defaults to refusing. The pacing is its own setting for the same
reason: at the ten seconds that space searches, a night of closes would take
over five hours, which is a different kind of load from what was agreed.

Everything else is shared with discovery on purpose -- the same identified user
agent, the same persistent ledger deciding whether a run may happen, the same
on-disk cache, and the same refusal to continue when the provider says it is
being asked too often.
"""

from __future__ import annotations

import http.client
import time
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from urllib.error import HTTPError, URLError
from urllib.request import Request

from ...config.provider import AcquisitionConfig, ProviderConfig
from ...http_safety import require_public_https
from ...listings.model import PendingLot, Settlement, SettleOutcome
from ...throttle import RequestThrottle
from ..http import (
    ACCEPTED_CONTENT,
    HTTP_OK,
    PollLedger,
    authorized_user_agent,
    enforce_request_limits,
    require_fetch_allowed,
    require_not_rate_limited,
)
from .discover import session_opener
from .parse import _product

# A page that 404s or 410s has no result to read and never will, so a run says
# so and moves on rather than failing the night over one withdrawn lot.
GONE_STATUSES = (404, 410)

# How many lots in a row may fail at the transport level before the pass
# decides the provider has stopped answering and stops asking.
#
# One failed socket is a fact about one request: this pass makes thousands of
# them and some will lose a connection. A run of them is a fact about the
# provider, and continuing is then thousands of requests into a closed door --
# rude, slow, and certain to fail. Ten is small enough to notice an outage
# quickly and large enough that an unlucky patch does not end a good night.
#
# Measured on 2026-10-19: without this, one "Remote end closed connection
# without response" at about lot 500 of 2,720 raised out of this function and
# took every reading gathered before it with it.
CONSECUTIVE_FAILURES_BEFORE_STOPPING = 10

# A transport failure, as distinct from a page that answered and could not be
# parsed. Kept apart from None because the two mean different things to the
# loop: one lot's page being broken says nothing about the next lot's, while a
# socket that will not open may be about every lot left.
UNREACHABLE = object()


def settle_lots(
    provider: ProviderConfig,
    config: AcquisitionConfig,
    lots: Iterable[PendingLot],
    *,
    instant: datetime | None = None,
    opener: Callable | None = None,
    sleeper: Callable[[float], None] = time.sleep,
) -> SettleOutcome:
    """Read each lot's own page and return the results of the ones that closed.

    A lot whose page still says it is open is left alone rather than recorded:
    bidding extends in thirty-second steps, so a lot read a moment too early is
    genuinely unfinished, and writing its live bid as a sale would reintroduce
    the exact error this exists to remove. It stays pending and the next run
    gets it.
    """
    pending = list(lots)
    # The settling gate answers first. Both refusals are real, but this is the
    # one specific to asking for a page per lot, and letting a more general
    # refusal mask it would hide the setting an operator has to decide about.
    require_settling_allowed(config)
    # Then the ordinary questions, asked of the provider's own address: whether
    # this program may contact it at all is a property of the configuration and
    # not of one lot. Each lot's own address is checked again below, because it
    # was parsed out of a page rather than configured.
    require_fetch_allowed(provider, config, config.url)
    if not pending:
        return SettleOutcome((), 0, 0, 0)

    user_agent = authorized_user_agent(config)
    opener = session_opener() if opener is None else opener
    moment = datetime.now(UTC) if instant is None else instant

    ledger = PollLedger.at(config.ledger_file)
    enforce_request_limits(ledger.attempts(), config, moment)
    # One settling run is one attempt however many lots it reads, exactly as a
    # discovery run is. Recorded before the first connection so an interrupted
    # run still counts against the day.
    ledger.record(moment)

    throttle = RequestThrottle(
        max_requests=len(pending),
        minimum_interval_seconds=float(config.seconds_between_settles),
        sleeper=sleeper,
    )
    settled: list[Settlement] = []
    still_open = missing = unreadable = unreachable = 0
    consecutive_failures = 0
    stopped_early = ""
    for lot in pending:
        page = _read_page(lot, config, user_agent, opener, throttle)
        if page is UNREACHABLE:
            # The lot stays owed, exactly as an unreadable page does, but it is
            # counted apart from one: nothing here answered, so there is no
            # page to blame and no parser to fix.
            unreachable += 1
            consecutive_failures += 1
            if consecutive_failures >= CONSECUTIVE_FAILURES_BEFORE_STOPPING:
                stopped_early = (
                    f"the provider stopped answering: "
                    f"{consecutive_failures} lots in a row could not be reached"
                )
                break
            continue
        consecutive_failures = 0
        if page is None:
            missing += 1
            continue
        result = _settlement_of(page, lot)
        if result is None:
            unreadable += 1
        elif result is OPEN_STILL:
            still_open += 1
        else:
            settled.append(result)
    return SettleOutcome(
        tuple(settled),
        still_open,
        missing,
        unreadable,
        unreachable,
        stopped_early=stopped_early,
    )


def require_settling_allowed(config: AcquisitionConfig) -> None:
    """Refuse before the first request unless this heavier read was agreed.

    Separate from the polling gate because it is a separate agreement. A lot
    per request is tens of times the load of a search sweep, so inheriting the
    sweep's permission would be helping itself to something nobody granted.
    """
    if not config.settle_authorized:
        raise RuntimeError(
            "reading a page per closed lot is not authorized; "
            "set settle_authorized = true under [provider.acquisition] "
            "once the provider has agreed to that load"
        )


# A sentinel kept distinct from None, which means "could not be read". A lot
# that is simply not finished yet is a normal outcome and not a failure.
OPEN_STILL = Settlement(
    source="",
    listing_id="",
    final_bid=Decimal(0),
    bid_count=0,
    closed_at=None,
    scheduled_close=None,
    extended=False,
)


def _read_page(
    lot: PendingLot,
    config: AcquisitionConfig,
    user_agent: str,
    opener: Callable,
    throttle: RequestThrottle,
):
    """Fetch one lot's page.

    Three answers, because the caller has to tell them apart: the page, None
    when there is no page to read and never will be, and UNREACHABLE when the
    request itself did not complete. Only the last one may be about the lots
    still to come.
    """
    try:
        # This address came out of a parsed page, so it is checked here rather
        # than trusted. One unusable address costs its own lot and not the run.
        require_public_https(lot.url)
    except (ValueError, RuntimeError):
        return None
    throttle.take_turn()
    request = Request(
        lot.url,
        headers={"User-Agent": user_agent, "Accept": ACCEPTED_CONTENT},
        method="GET",
    )
    try:
        with opener(request, timeout=config.timeout_seconds) as response:
            if int(response.status) != HTTP_OK:
                return None
            return response.read().decode("utf-8", errors="replace")
    except HTTPError as error:
        if error.code in GONE_STATUSES:
            return None
        # Being told to slow down is the one answer that must still end the
        # run, and it raises so that no later code can mistake it for a lot
        # that merely failed. The caller stores what it gathered first.
        require_not_rate_limited(error)
        return UNREACHABLE
    except (URLError, http.client.HTTPException, OSError):
        # The request did not complete: a closed connection, a reset, a
        # timeout, a name that would not resolve. None of that is a fact about
        # this lot, so it costs the lot and the pass counts it toward deciding
        # whether the provider has stopped answering altogether.
        return UNREACHABLE


def _settlement_of(html: str, lot: PendingLot) -> Settlement | None:
    """Read a settled result, OPEN_STILL if unfinished, None if unreadable."""
    try:
        product = _product(html)
    except (ValueError, KeyError):
        return None
    if not product.get("isClosed"):
        return OPEN_STILL
    final_bid = _amount(product.get("currentPrice"))
    if final_bid is None:
        return None
    closed_at = _moment(product.get("closeTime"))
    scheduled = _moment(product.get("initialCloseTime")) or _moment(lot.scheduled_close)
    return Settlement(
        source=lot.source,
        listing_id=lot.listing_id,
        final_bid=final_bid,
        bid_count=int(product.get("bidCount") or 0),
        closed_at=closed_at,
        scheduled_close=scheduled,
        # Trust the provider's own word where it gives one, and fall back to
        # comparing the two times, which is the same question asked indirectly.
        extended=bool(product.get("projectExtended"))
        or (closed_at is not None and scheduled is not None and closed_at > scheduled),
    )


def _amount(value: object) -> Decimal | None:
    """A stated price as an exact decimal, or None when there is no number."""
    if value is None:
        return None
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return amount if amount >= 0 else None


def _moment(value: object) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
