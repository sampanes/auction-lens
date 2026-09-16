"""What a report says, decided once, before anything decides how it looks.

The plain-text and HTML reports describe the same findings. When each of them
walked a candidate itself, they were free to drift: one of them showed stated
retail, the other showed the pickup location, and nothing noticed. So the
question "what does the report say" is answered here, exactly once, and those
renderers only answer "what does that look like in this medium".

Nothing in this module knows about terminals, markup, or escaping.
"""

from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime
from zoneinfo import ZoneInfo

from ..listings.conditions import Tag
from ..listings.model import Listing
from ..matching.logistics import LogisticsStatus
from ..matching.model import Candidate, InterestHarvest, ReadingOrder, ranked
from ..matching.progress import InterestProgress
from ..matching.searches import SearchHint
from ..pricing.model import ValuationBand, ValuationSummary
from .records import (
    NO_DELIVERY_FILTER,
    DeliverySummary,
    Finding,
    Group,
    Handling,
    Link,
    ListingFacts,
    OutcomeSummary,
    Photo,
    Report,
    Valuation,
)

EMPTY_REPORT = "Auction Lens found no listings meeting the configured criteria."
EMPTY_DELIVERY = "Auction Lens found no new or price-changed listings for this destination."

# Day, hour, and the zone's own name: enough to act on, short enough to sit on
# one line. The zone is named because a report is read wherever the reader is.
CLOSING_TIME_FORMAT = "%a %H:%M %Z"

NEW_LABEL = "New"
PRICE_CHANGED_LABEL = "Price changed"
SEEN_LABEL = "Seen"

NO_CONDITIONS = "none listed"

UNREVIEWED_WIN = (
    "Action needed: {count} won {lots} {have} an unreviewed finite-interest "
    "match; review {them} with watchlist --verdict won, then use watch "
    "--fulfills or watch --clear-fulfillments."
)

FEEDBACK_HINT = (
    "Optional feedback: copy a Watch key into "
    "`auction-lens feedback yes --key WATCH-KEY`; run "
    "`auction-lens feedback --help` for specific reasons."
)


def build_report(
    candidates: list[Candidate],
    zone: ZoneInfo,
    *,
    searches: tuple[SearchHint, ...] = (),
    order: ReadingOrder = ReadingOrder.PRIORITY,
    interest_progress: tuple[InterestProgress, ...] = (),
    unreviewed_wins: int = 0,
    delivery: DeliverySummary = NO_DELIVERY_FILTER,
    harvest: tuple[InterestHarvest, ...] = (),
    notices: tuple[str, ...] = (),
) -> Report:
    """Turn scored candidates into everything a report has to say about them.

    The zone is the provider's, because a closing time is a fact about the
    auction rather than about whoever opens the mail.

    Everything after it must be named. This is the only function left that
    takes the whole bundle, so it is the only place a caller could put two of
    them the wrong way round, and naming them makes that impossible rather
    than merely unlikely.
    """
    outcomes = build_outcome_summary(interest_progress, unreviewed_wins)
    if not candidates:
        headline = (
            EMPTY_DELIVERY
            if delivery.active and not delivery.repeated
            else EMPTY_REPORT
        )
        return Report(
            headline=headline,
            notices=notices,
            outcomes=outcomes,
            delivery=delivery,
        )

    ordered = ranked(candidates, order=order)
    priority_ranks: dict[int, deque[int]] = defaultdict(deque)
    for position, candidate in enumerate(ranked(candidates)):
        priority_ranks[id(candidate)].append(position)
    findings = tuple(
        _finding(candidate, zone, priority_ranks[id(candidate)].popleft())
        for candidate in ordered
    )
    sections = _by_section(ordered, findings)
    first_close = closing_time(soonest_close(candidates), zone)
    return Report(
        headline=_headline(len(findings), first_close),
        findings=findings,
        first_close=first_close,
        searches=_hints_without_a_section(searches, set(sections)),
        notices=notices,
        outcomes=outcomes,
        delivery=delivery,
        feedback_hint=FEEDBACK_HINT,
        groups=tuple(
            _group(name, items, harvest, searches)
            for name, items in sections.items()
        ),
    )


def _group(
    name: str,
    items: list[Finding],
    harvest: tuple[InterestHarvest, ...],
    searches: tuple[SearchHint, ...],
) -> Group:
    """One section, carrying what it is not showing along with what it is."""
    withheld = next((tally.withheld for tally in harvest if tally.name == name), 0)
    return Group(
        title=readable(name),
        findings=tuple(items),
        withheld=withheld,
        # A phrase is only a shortcut when there is something to reach with it.
        searches=tuple(hint for hint in searches if hint.rule == name) if withheld else (),
    )


def _hints_without_a_section(
    searches: tuple[SearchHint, ...], sections: set[str]
) -> tuple[SearchHint, ...]:
    """Phrases for kinds that are not on the page at all.

    A rule with a section has already said everything it needs to say, in that
    section, whether or not it is holding anything back. This is the remainder:
    a rule that earned a phrase but whose lots did not survive the report's own
    cap. Without a footer those lots would be unreachable and unmentioned.
    """
    return tuple(hint for hint in searches if hint.rule not in sections)


def build_outcome_summary(
    progress: tuple[InterestProgress, ...], unreviewed_wins: int
) -> OutcomeSummary:
    """Say only what outcomes can establish without guessing intent.

    An unlimited interest has no finish line and therefore no useful progress
    fraction. A finite match is also never allocated implicitly: the warning
    asks the person who knows which want the purchase actually fulfilled.
    """
    finite = tuple(_progress_line(item) for item in progress if item.is_limited)
    warning = ""
    if unreviewed_wins:
        singular = unreviewed_wins == 1
        warning = UNREVIEWED_WIN.format(
            count=unreviewed_wins,
            lots="lot" if singular else "lots",
            have="has" if singular else "have",
            them="it" if singular else "them",
        )
    return OutcomeSummary(progress=finite, warning=warning)


def _progress_line(progress: InterestProgress) -> str:
    """A compact status for one finite want, using its remembered display name.

    The numerator counts explicit allocations on lots whose verdict is WON. It
    does not count every win, so name the human decision rather than the verdict.
    """
    wanted = progress.wanted
    if wanted is None:  # Kept total even if called independently in a future refactor.
        return ""
    state = "retired" if progress.is_retired else f"{progress.remaining} remaining"
    return (
        f"{progress.interest.name}: {progress.fulfilled}/{wanted} fulfilled; {state}"
    )


def _headline(match_count: int, first_close: str) -> str:
    """How many, and how long there is before the first one is gone.

    The deadline belongs in the first line because it is the only fact that
    decides whether the rest is worth reading now or after dinner.
    """
    if not first_close:
        return f"Auction Lens found {match_count} match(es)."
    return (
        f"Auction Lens found {match_count} match(es); "
        f"the first closes {first_close}."
    )


def soonest_close(candidates: list[Candidate]) -> datetime | None:
    """When the earliest-closing reported lot goes, or None if none says."""
    times = [
        candidate.listing.ends_at
        for candidate in candidates
        if candidate.listing.ends_at is not None
    ]
    return min(times) if times else None


def closing_time(ends_at: datetime | None, zone: ZoneInfo) -> str:
    """When bidding ends, in the provider's local time, or "" if unstated.

    Every lot seen so far states one, so an empty answer means the page changed
    shape rather than that this lot runs forever. The projection retains that
    absence so each medium can say or omit it honestly.
    """
    if ends_at is None:
        return ""
    return ends_at.astimezone(zone).strftime(CLOSING_TIME_FORMAT)


def readable(identifier: str) -> str:
    """Turn a stored identifier such as needs_plan into Needs Plan."""
    return identifier.replace("_", " ").title()


def _by_section(
    candidates: list[Candidate], findings: tuple[Finding, ...]
) -> dict[str, list[Finding]]:
    """Group an already ordered projection by what each finding is one of.

    Ordering only, never selection: which lots are worth reporting was
    already decided against the bars, and a reader preferring to see the
    dearest thing first must not quietly change what reached the page.

    Sections arrive in the order their best lot did, so the strongest thing
    found today is still the first thing read.
    """
    grouped: dict[str, list[Finding]] = defaultdict(list)
    for candidate, finding in zip(candidates, findings, strict=True):
        grouped[candidate.section].append(finding)
    return grouped


def _finding(candidate: Candidate, zone: ZoneInfo, priority_rank: int) -> Finding:
    return Finding(
        title=candidate.listing.title,
        change=_change(candidate),
        score=candidate.score,
        priority_rank=priority_rank,
        facts=_listing_facts(candidate, zone),
        reasons=candidate.reasons,
        url=candidate.listing.url,
        photos=_photos(candidate),
        handling=_handling(candidate),
        valuation=_valuation(candidate.valuation),
    )


def _photos(candidate: Candidate) -> tuple[Photo, ...]:
    """Name the two useful ends of a gallery without showing one image twice."""
    stock = _https_photo(candidate.listing.stock_photo_url)
    actual = _https_photo(candidate.listing.condition_photo_url)
    if stock and stock == actual:
        # One photo in the gallery, so neither label would be a claim we can
        # make about it. Say only what is certain: it came from the listing.
        return (Photo("Listing photo", stock),)
    photos = []
    if stock:
        photos.append(Photo("Product photo", stock))
    if actual:
        photos.append(Photo("Actual lot", actual))
    return tuple(photos)


def _https_photo(url: str) -> str:
    """Keep email images remote and encrypted; omit anything else."""
    return url if url.lower().startswith("https://") else ""


def _change(candidate: Candidate) -> str:
    """Say how this listing relates to what the database already knew."""
    if candidate.change.is_new:
        return NEW_LABEL
    if candidate.change.price_changed:
        if candidate.change.previous_bid is not None:
            return f"{PRICE_CHANGED_LABEL} from ${candidate.change.previous_bid}"
        return PRICE_CHANGED_LABEL
    return SEEN_LABEL


def _listing_facts(candidate: Candidate, zone: ZoneInfo) -> ListingFacts:
    """Project every listing value a report medium may need exactly once."""
    listing = candidate.listing
    return ListingFacts(
        bid=f"${listing.current_bid}",
        total_cost=f"${candidate.total_cost}",
        retail=f"${listing.estimated_retail}" if listing.estimated_retail else "",
        retail_ratio=(
            f"{candidate.retail_ratio:.0%}" if candidate.retail_ratio is not None else ""
        ),
        closes=closing_time(listing.ends_at, zone),
        location=listing.location,
        conditions=_conditions(listing),
        condition_severity=_condition_severity(listing),
        watch_key=listing.key,
    )


def _conditions(listing: Listing) -> str:
    """One condition sentence shared by full reports and compact cards."""
    return ", ".join(listing.conditions) or NO_CONDITIONS


def _condition_severity(listing: Listing) -> Tag:
    """The most concerning provider tag, used only to colour compact cards."""
    tags = {tag.tag for tag in listing.grade.tags} if listing.grade else set()
    for severity in (Tag.RED, Tag.AMBER):
        if severity in tags:
            return severity
    return Tag.GREEN


def _handling(candidate: Candidate) -> Handling:
    """Ask an open question, report a settled one, or say nothing at all."""
    assessment = candidate.logistics
    if assessment is None or assessment.status == LogisticsStatus.ORDINARY:
        return Handling()
    if assessment.status == LogisticsStatus.NEEDS_PLAN:
        return Handling(
            questions=assessment.questions,
            decision_key=candidate.listing.key,
        )
    return Handling(
        summary=readable(assessment.status),
        note=assessment.decision_note,
    )


def _valuation(summary: ValuationSummary | None) -> Valuation:
    if summary is None:
        return Valuation()
    return Valuation(
        bands=tuple(_band(band) for band in summary.bands),
        research=tuple(
            Link(label=link.label, url=link.url) for link in summary.research_links
        ),
        warnings=tuple(summary.errors),
    )


def _band(band: ValuationBand) -> str:
    return (
        f"{readable(band.basis)}: ${band.low}-${band.high} "
        f"(typical ${band.typical}; {band.source_count} source(s), "
        f"{band.sample_size} comp(s))"
    )
