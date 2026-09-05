from __future__ import annotations

import html
import os
import smtplib
from collections import defaultdict
from email.message import EmailMessage
from pathlib import Path

from .config import EmailConfig
from .models import Candidate


def load_env_file(path: str | Path) -> None:
    """Load simple KEY=VALUE settings without overriding the process environment."""
    source = Path(path)
    if not source.exists():
        return
    for number, raw_line in enumerate(source.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"invalid environment line {number} in {source}")
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key and value:
            os.environ.setdefault(key, value)


def render_text(candidates: list[Candidate]) -> str:
    if not candidates:
        return "Auction Lens found no listings meeting the configured criteria."
    grouped: dict[str, list[Candidate]] = defaultdict(list)
    for candidate in sorted(candidates, key=lambda item: item.score, reverse=True):
        grouped[candidate.category].append(candidate)
    lines = [f"Auction Lens found {len(candidates)} match(es)."]
    for category, items in grouped.items():
        lines.extend(("", category.upper()))
        for item in items:
            listing = item.listing
            marker = "NEW" if item.change.is_new else "PRICE CHANGED" if item.change.price_changed else "SEEN"
            lines.extend(
                (
                    f"[{marker}] {listing.title}",
                    f"Score {item.score} | bid ${listing.current_bid} | estimated total ${item.total_cost}",
                    f"Location: {listing.location or 'unknown'} | Conditions: {', '.join(listing.conditions) or 'none listed'}",
                    f"Why: {'; '.join(item.reasons)}",
                )
            )
            if item.logistics and item.logistics.status != "ordinary":
                if item.logistics.status == "needs_plan":
                    lines.extend(f"LOGISTICS CHECK: {question}" for question in item.logistics.questions)
                    lines.append(
                        f"Decision key: {listing.source}/{listing.listing_id}"
                    )
                else:
                    label = item.logistics.status.replace("_", " ").title()
                    details = (
                        f" | {item.logistics.decision_note}"
                        if item.logistics.decision_note
                        else ""
                    )
                    lines.append(f"Logistics: {label}{details}")
            lines.append(listing.url)
            if item.valuation:
                for band in item.valuation.bands:
                    lines.append(
                        f"{band.basis.replace('_', ' ').title()}: "
                        f"${band.low}-${band.high} (typical ${band.typical}; "
                        f"{band.source_count} source(s), {band.sample_size} comp(s))"
                    )
                if item.valuation.research_links:
                    lines.append(
                        "Research: "
                        + " | ".join(
                            f"{link.label}: {link.url}"
                            for link in item.valuation.research_links
                        )
                    )
                for error in item.valuation.errors:
                    lines.append(f"Valuation source unavailable: {error}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_html(candidates: list[Candidate]) -> str:
    if not candidates:
        return "<p>Auction Lens found no listings meeting the configured criteria.</p>"
    cards = []
    for item in sorted(candidates, key=lambda candidate: candidate.score, reverse=True):
        listing = item.listing
        marker = "New" if item.change.is_new else "Price changed" if item.change.price_changed else "Seen"
        cards.append(
            "<article style='border:1px solid #ddd;border-radius:8px;padding:14px;margin:12px 0'>"
            f"<h3 style='margin-top:0'>{html.escape(listing.title)}</h3>"
            f"<p><strong>{item.category.title()} &middot; Score {item.score} &middot; {marker}</strong></p>"
            f"<p>Bid ${listing.current_bid} &middot; Estimated total ${item.total_cost}"
            + (f" &middot; Retail ${listing.estimated_retail}" if listing.estimated_retail else "")
            + "</p>"
            + f"<p>{html.escape('; '.join(item.reasons))}</p>"
            + _logistics_html(item)
            + _valuation_html(item)
            + f"<p><a href='{html.escape(listing.url, quote=True)}'>View listing</a></p>"
            + "</article>"
        )
    return f"<h2>Auction Lens: {len(candidates)} match(es)</h2>{''.join(cards)}"


def _logistics_html(candidate: Candidate) -> str:
    assessment = candidate.logistics
    if not assessment or assessment.status == "ordinary":
        return ""
    if assessment.status == "needs_plan":
        questions = "".join(
            f"<li>{html.escape(question)}</li>" for question in assessment.questions
        )
        key = html.escape(f"{candidate.listing.source}/{candidate.listing.listing_id}")
        return f"<p><strong>Logistics check</strong></p><ul>{questions}</ul><p>Decision key: {key}</p>"
    label = html.escape(assessment.status.replace("_", " ").title())
    note = f" &middot; {html.escape(assessment.decision_note)}" if assessment.decision_note else ""
    return f"<p><strong>Logistics:</strong> {label}{note}</p>"


def _valuation_html(candidate: Candidate) -> str:
    if not candidate.valuation:
        return ""
    rows = "".join(
        "<li>"
        f"{html.escape(band.basis.replace('_', ' ').title())}: "
        f"${band.low}&ndash;${band.high} (typical ${band.typical}; "
        f"{band.source_count} source(s), {band.sample_size} comp(s))"
        "</li>"
        for band in candidate.valuation.bands
    )
    links = " | ".join(
        f"<a href='{html.escape(link.url, quote=True)}'>{html.escape(link.label)}</a>"
        for link in candidate.valuation.research_links
    )
    errors = "".join(f"<li>{html.escape(error)}</li>" for error in candidate.valuation.errors)
    return (
        (f"<ul>{rows}</ul>" if rows else "")
        + (f"<p>Research: {links}</p>" if links else "")
        + (f"<p>Valuation sources unavailable:</p><ul>{errors}</ul>" if errors else "")
    )


def send_email(candidates: list[Candidate], config: EmailConfig) -> None:
    if config.security not in {"ssl", "starttls"}:
        raise ValueError("email security must be 'ssl' or 'starttls'")
    values = {
        "host": os.getenv(config.host_env),
        "username": os.getenv(config.username_env),
        "password": os.getenv(config.password_env),
        "sender": os.getenv(config.sender_env),
        "recipient": os.getenv(config.recipient_env),
    }
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise RuntimeError(f"missing email environment settings: {', '.join(missing)}")

    message = EmailMessage()
    message["Subject"] = config.subject.replace("{{ match_count }}", str(len(candidates)))
    message["From"] = values["sender"]
    message["To"] = values["recipient"]
    message.set_content(render_text(candidates))
    message.add_alternative(render_html(candidates), subtype="html")

    smtp_class = smtplib.SMTP_SSL if config.security == "ssl" else smtplib.SMTP
    with smtp_class(values["host"], config.port, timeout=30) as smtp:
        if config.security == "starttls":
            smtp.starttls()
        smtp.login(values["username"], values["password"])
        smtp.send_message(message)
