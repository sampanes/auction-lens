"""Getting a finished report to a destination, and remembering that it arrived.

Two commands send things -- the daily findings and the watchlist -- and both
have the same problem: the transport can succeed while the local receipt fails,
and a person must never be told a clean story about an ambiguous one. That
reasoning lives here once rather than in each command.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from ..config import AppConfig
from ..models import Candidate, harvest_of
from ..notifications import (
    DeliveryChannel,
    DeliveryRoute,
    ReportKind,
    candidate_items,
    outcome_fingerprint,
    plan_candidates,
)
from ..pipeline import RunResult, follow_candidates
from ..reporting import (
    DeliverySummary,
    build_report,
    email_destination,
    send_email,
    send_webhook,
    webhook_destination,
)
from ..reporting.webhook import webhook_item_limit
from ..storage import DeliveryLedger, WatchlistStore


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
    result: RunResult,
    watchlist: WatchlistStore,
    destinations: dict[DeliveryChannel, str],
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
    summary = outcome_fingerprint(result.interest_progress, result.unreviewed_wins)
    additionally_followed = 0
    failures = []
    for channel, fingerprint in destinations.items():
        accepted = False
        phase = "receipt planning"
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
                    held_back_matches=plan.held_back_matches,
                )
                phase = "transport"
                _send_findings(channel, plan.candidates, config, result, note)
                accepted = True
                phase = "local receipt"
                additionally_followed += follow_candidates(
                    plan.candidates,
                    result.all_candidates,
                    watchlist,
                )
                delivery.accept(plan.receipts, summary)
            _report_delivery_sent(channel, len(plan.candidates), config)
        except (OSError, RuntimeError, ValueError) as error:
            failures.append(delivery_failure(channel, accepted, phase, error))
    return additionally_followed, failures


def _delivery_limit(channel: DeliveryChannel, config: AppConfig) -> int | None:
    """Use the exact limit the selected transport will actually render."""
    if channel == DeliveryChannel.WEBHOOK:
        return webhook_item_limit(config.webhook, config.reports.max_items)
    return config.reports.max_items


def _send_findings(
    channel: DeliveryChannel,
    candidates: tuple[Candidate, ...],
    config: AppConfig,
    result: RunResult,
    delivery: DeliverySummary,
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


def delivery_failure(
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
