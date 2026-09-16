"""Record recommendation feedback without changing the facts it comments on."""

from __future__ import annotations

import argparse
from pathlib import Path

from ..config.load import load_config
from ..feedback.artifacts import save_feedback_proposal
from ..feedback.model import FeedbackLabel, FeedbackReview
from ..feedback.record import (
    clear_feedback,
    configuration_sha256,
    infer_feedback_target,
    record_feedback,
)
from ..feedback.review import review_feedback
from ..feedback.store import FeedbackStore
from ..watchlist.store import WatchlistStore
from .exit_codes import SUCCESS
from .lot_key import lot_identity
from .parser import CLEAR, REVIEW

UNCHANGED_NOTICE = (
    "Watch verdict, logistics decision, and configuration were not changed."
)


def feedback(args: argparse.Namespace) -> int:
    """Dispatch the two honest modes: record one answer, or review a pattern."""
    if args.action == REVIEW:
        return _review(args)
    return _record(args)


def _record(args: argparse.Namespace) -> int:
    """Append one event only after the lot and matched interest are unambiguous."""
    review_only = set(getattr(args, "_stated_options", ())) & {
        "--minimum-evidence",
        "--proposal-dir",
    }
    if args.save:
        review_only.add("--save")
    if review_only:
        raise ValueError(
            "feedback record actions cannot use review-only flags: "
            + ", ".join(sorted(review_only))
        )
    source, listing_id = lot_identity(args)
    item = WatchlistStore(Path(args.watchlist)).get(source, listing_id)
    if item is None:
        raise ValueError(
            f"{source}/{listing_id} is not in {args.watchlist}; "
            "feedback can only describe a known watched item"
        )

    # Resolve before opening the event log. Ambiguous feedback must not leave a
    # half-written private file behind.
    target = infer_feedback_target(item, interest=args.interest)
    store = FeedbackStore(Path(args.feedback_file))
    digest = configuration_sha256(Path(args.config))
    if args.action == CLEAR:
        if args.note is not None:
            raise ValueError("feedback clear cannot be combined with --note")
        event = clear_feedback(
            store,
            item,
            digest,
            interest=args.interest,
        )
        result = (
            f"Cleared feedback for {item.key} / {target.name}."
            if event is not None
            else f"Feedback was already clear for {item.key} / {target.name}."
        )
    else:
        event = record_feedback(
            store,
            item,
            FeedbackLabel(args.action),
            digest,
            interest=args.interest,
            note=args.note or "",
        )
        result = (
            f"Recorded {args.action} for {item.key} / {target.name}."
            if event is not None
            else f"Feedback already says {args.action} for {item.key} / {target.name}."
        )

    print(result)
    print(UNCHANGED_NOTICE)
    return SUCCESS


def _review(args: argparse.Namespace) -> int:
    """Render derived evidence; saving preserves it but never applies it."""
    stated_record_flags = [
        flag
        for flag, value in (
            ("--key", args.key),
            ("--source", args.source),
            ("--listing-id", args.listing_id),
            ("--interest", args.interest),
            ("--note", args.note),
        )
        if value is not None
    ]
    if "--watchlist" in getattr(args, "_stated_options", ()):
        stated_record_flags.append("--watchlist")
    if stated_record_flags:
        raise ValueError(
            "feedback review cannot use record-only flags: "
            + ", ".join(stated_record_flags)
        )
    if args.minimum_evidence < 1:
        raise ValueError("--minimum-evidence must be at least 1")

    config = load_config(args.config)
    digest = configuration_sha256(Path(args.config))
    result = review_feedback(
        FeedbackStore(Path(args.feedback_file)),
        config.interests,
        minimum_distinct_items=args.minimum_evidence,
        config_sha256=digest,
    )
    print(_render_review(result, minimum_evidence=args.minimum_evidence), end="")

    if args.save:
        if not result.proposals:
            print("No proposal was available to save.")
        for proposal in result.proposals:
            path = save_feedback_proposal(proposal, Path(args.proposal_dir))
            print(f"Saved proposal: {path}")
    return SUCCESS


def _render_review(review: FeedbackReview, *, minimum_evidence: int) -> str:
    """Give the terminal the same conservative facts the core review derived."""
    lines = ["Feedback review"]
    if not review.patterns:
        lines.append(
            f"No repeated feedback met the {minimum_evidence}-item threshold."
        )
    else:
        lines.extend(f"- {pattern.summary}" for pattern in review.patterns)

    if review.proposals:
        lines.append("Reviewable proposals:")
        for proposal in review.proposals:
            lines.append(f"- {proposal.target.name} ({proposal.proposal_id})")
            for change in proposal.changes:
                before = "not set" if change.before is None else str(change.before)
                lines.append(f"  {change.field}: {before} -> {change.after}")
    else:
        lines.append("No conservative configuration proposal is supported yet.")
    lines.append(review.notice)
    return "\n".join(lines) + "\n"
