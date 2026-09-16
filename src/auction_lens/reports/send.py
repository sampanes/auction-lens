"""Getting a finished report to a destination, and remembering that it arrived.

Two commands send things -- the daily findings and the watchlist -- and both
have the same problem: the transport can succeed while the local receipt fails,
and a person must never be told a clean story about an ambiguous one. That
reasoning lives here once rather than in each command.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from ..config.app import AppConfig
from ..matching.analyze import AnalysisResult, follow_candidates
from ..matching.model import Candidate, harvest_of
from ..watchlist.model import WatchedItem
from ..watchlist.store import WatchlistStore
from .delivery import (
    DeliveryChannel,
    DeliveryRoute,
    ReportKind,
    candidate_items,
    outcome_fingerprint,
    plan_candidates,
    plan_watchlist,
    watchlist_items,
)
from .destinations import destination_fingerprint
from .email import email_destination, send_email, send_watchlist_email
from .findings import build_report
from .receipts import DeliveryLedger
from .records import DeliverySummary
from .webhook import (
    send_webhook,
    webhook_destination,
    webhook_item_limit,
)


@dataclass
class _DeliveryAttempt:
    """Remember which side of the remote/local ambiguity an attempt reached."""

    channel: DeliveryChannel
    accepted: bool = False
    phase: str = "receipt planning"

    @contextmanager
    def transport(self) -> Iterator[None]:
        """Cross the remote boundary, then mark later failures as local."""
        self.phase = "transport"
        yield
        self.accepted = True
        self.phase = "local receipt"

    def failure(self, error: BaseException) -> str:
        """Describe the safe recovery for the furthest completed phase."""
        return _delivery_failure(self.channel, self.accepted, self.phase, error)


def preflight_reports(
    config: AppConfig, args: argparse.Namespace
) -> dict[DeliveryChannel, str]:
    """Resolve destinations and the private ledger before local state changes."""
    email_requested = getattr(args, "email", False)
    webhook_requested = getattr(args, "webhook", False)
    if getattr(args, "repeat_delivery", False) and not (
        email_requested or webhook_requested
    ):
        raise ValueError("--repeat-delivery requires --email or --webhook")

    destinations = {}
    if email_requested:
        destinations[DeliveryChannel.EMAIL] = email_destination(config.email)
    if webhook_requested:
        destinations[DeliveryChannel.WEBHOOK] = webhook_destination(config.webhook)
    if destinations:
        DeliveryLedger(Path(args.delivery_ledger)).check_ready()
    return destinations


def deliver_findings(
    args: argparse.Namespace,
    config: AppConfig,
    result: AnalysisResult,
    watchlist: WatchlistStore,
    destinations: dict[DeliveryChannel, str],
    *,
    notices: tuple[str, ...] = (),
) -> tuple[int, list[str]]:
    """Send each route independently and remember only accepted reports.

    A failed webhook must not erase a successful email receipt. On the next
    scheduled retry, email is therefore skipped while the webhook is tried
    again. A remote may accept immediately before the connection, process, or
    local commit fails; that ambiguous gap is reported honestly because no
    local ledger can close it.
    """
    if not destinations:
        return 0, []

    ledger = DeliveryLedger(Path(args.delivery_ledger))
    summary = outcome_fingerprint(
        result.interest_progress,
        result.unreviewed_wins,
        notices=notices,
    )
    additionally_followed = 0
    failures = []
    for channel, fingerprint in destinations.items():
        attempt = _DeliveryAttempt(channel)
        try:
            route = DeliveryRoute(ReportKind.FINDINGS, channel, fingerprint)
            with ledger.session(route) as delivery:
                proposed = candidate_items(result.all_candidates)
                plan = plan_candidates(
                    result.all_candidates,
                    delivery.revisions(proposed),
                    limit=_delivery_limit(channel, config),
                    repeat=args.repeat_delivery,
                    most_each=config.reports.most_per_interest,
                )
                summary_changed = delivery.summary_changed(summary)
                if not plan.candidates and not summary_changed and not args.repeat_delivery:
                    _report_delivery_current(channel, plan.unchanged_matches)
                    continue

                note = DeliverySummary(
                    active=True,
                    repeated=args.repeat_delivery,
                    unchanged_matches=plan.unchanged_matches,
                    unchanged_titles=tuple(
                        candidate.listing.title for candidate in plan.unchanged
                    ),
                    held_back_matches=plan.held_back_matches,
                )
                with attempt.transport():
                    _send_findings(
                        channel,
                        plan.candidates,
                        config,
                        result,
                        note,
                        notices,
                    )
                additionally_followed += follow_candidates(
                    plan.candidates,
                    result.all_candidates,
                    watchlist,
                )
                delivery.accept(plan.receipts, summary)
            _report_delivery_sent(channel, len(plan.candidates), config)
        except (OSError, RuntimeError, ValueError) as error:
            failures.append(attempt.failure(error))
    return additionally_followed, failures


def deliver_watchlist_email(
    args: argparse.Namespace,
    config: AppConfig,
    items: tuple[WatchedItem, ...],
) -> None:
    """Email one selected watchlist and save its receipt after acceptance."""
    destination = preflight_reports(config, args)[DeliveryChannel.EMAIL]
    selector = "all" if args.verdict is None else str(args.verdict)
    # Each public selector is a separate recurring report. Combining it with
    # the opaque destination keeps those receipt streams separate without
    # retaining either private destination value.
    route = DeliveryRoute(
        ReportKind.WATCHLIST,
        DeliveryChannel.EMAIL,
        destination_fingerprint(
            f"{destination}\0watchlist-selection={selector}"
        ),
    )
    attempt = _DeliveryAttempt(DeliveryChannel.EMAIL)
    try:
        ledger = DeliveryLedger(Path(args.delivery_ledger))
        with ledger.session(route) as delivery:
            proposed = watchlist_items(items)
            plan = plan_watchlist(
                items,
                delivery.revisions(proposed),
                repeat=args.repeat_delivery,
            )
            if not plan.items and not args.repeat_delivery:
                print(
                    "Watchlist email is up to date; "
                    f"{plan.unchanged_items} unchanged selected lot(s) were "
                    "already delivered."
                )
                return

            note = DeliverySummary(
                active=True,
                repeated=args.repeat_delivery,
                unchanged_matches=plan.unchanged_items,
                item_singular="selected lot",
                item_plural="selected lots",
            )
            with attempt.transport():
                send_watchlist_email(plan.items, config.email, note)
            delivery.accept(plan.receipts, outcome_fingerprint((), 0))
        print(f"Emailed {len(plan.items)} selected lot(s).")
    except (OSError, RuntimeError, ValueError) as error:
        raise RuntimeError(attempt.failure(error)) from error


def _delivery_limit(channel: DeliveryChannel, config: AppConfig) -> int | None:
    """Use the exact limit the selected transport will actually render."""
    if channel == DeliveryChannel.WEBHOOK:
        return webhook_item_limit(config.webhook, config.reports.max_items)
    return config.reports.max_items


def _send_findings(
    channel: DeliveryChannel,
    candidates: tuple[Candidate, ...],
    config: AppConfig,
    result: AnalysisResult,
    delivery: DeliverySummary,
    notices: tuple[str, ...],
) -> None:
    """Cross one transport boundary; its caller owns receipt persistence."""
    selected = list(candidates)
    report = build_report(
        selected,
        config.acquisition.zone,
        searches=result.searches,
        order=config.reports.order,
        interest_progress=result.interest_progress,
        unreviewed_wins=result.unreviewed_wins,
        delivery=delivery,
        notices=notices,
        # Counted against what this destination is actually being sent, so a
        # suppressed lot is not described as one still on the page.
        harvest=harvest_of(list(result.all_candidates), selected),
    )
    if channel == DeliveryChannel.EMAIL:
        send_email(report, config.email)
        return
    send_webhook(report, config.webhook)


def _report_delivery_current(channel: DeliveryChannel, unchanged: int) -> None:
    noun = "Email" if channel == DeliveryChannel.EMAIL else "Webhook"
    detail = (
        f" {unchanged} unchanged match(es) were already delivered."
        if unchanged
        else " Its outcome summary is unchanged."
    )
    print(f"{noun} report is up to date.{detail}")


def _report_delivery_sent(
    channel: DeliveryChannel, count: int, config: AppConfig
) -> None:
    if channel == DeliveryChannel.EMAIL:
        # Name the variable, not the address; the ledger follows the same rule.
        print(f"Emailed {count} match(es) to {config.email.recipient_env}.")
    else:
        print(f"Posted {count} match(es) to the webhook.")


def _delivery_failure(
    channel: DeliveryChannel,
    accepted: bool,
    phase: str,
    error: BaseException,
) -> str:
    """Explain recovery without copying a private transport error into logs."""
    name = channel.value
    kind = type(error).__name__
    if accepted:
        return (
            f"{name} was accepted, but its local receipt could not be saved; "
            f"a retry may repeat it. Check the watchlist and --delivery-ledger "
            f"paths. [{kind}]"
        )
    if phase == "transport":
        settings = (
            "SMTP settings"
            if channel == DeliveryChannel.EMAIL
            else "webhook settings"
        )
        return (
            f"{name} delivery did not finish cleanly; no receipt was saved. "
            "If the remote accepted it before the connection failed, a retry may "
            f"repeat it. Check {settings} and connectivity. [{kind}]"
        )
    return (
        f"{name} was not attempted because receipt planning failed; check "
        f"--delivery-ledger. [{kind}]"
    )
