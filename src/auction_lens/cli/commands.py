"""One function per command, each doing only what its name says."""

from __future__ import annotations

import argparse
import sys
import warnings
from dataclasses import replace
from getpass import GetPassWarning, getpass
from pathlib import Path

from ..acquisition import (
    METADATA_SUFFIX,
    check_discovery_ready,
    discover_searches,
    fetch_authorized_page,
)
from ..config import AppConfig, EmailConfig, RunMode, load_config
from ..env_file import write_settings
from ..fields import parse_money
from ..file_io import read_json, write_json_atomically
from ..ingest import load_listings, read_saved_page, read_search_page, unique_lots
from ..models import LogisticsDecision, LogisticsStatus, WatchedItem
from ..pipeline import analyze_listings
from ..reporting import (
    check_email_ready,
    render_text,
    render_watchlist,
    send_email,
    send_watchlist_email,
    send_webhook,
)
from ..reporting.webhook import webhook_address
from ..storage import (
    Database,
    LogisticsDecisionStore,
    ObservationStore,
    WatchlistStore,
)
from ..valuation import ValuationEngine
from .parser import CLEAR, DEFAULT_INBOX, DROP, EXAMPLE_CONFIG, PROGRAM

# The host most people setting this up are reaching for; compatible hosts are accepted.
DEFAULT_SMTP_HOST = "smtp.gmail.com"

PAGE_SUFFIX = ".html"
LISTINGS_KEY = "listings"

SUCCESS = 0


ENV_TEMPLATE = """# Local settings for Auction Lens. Ignored by git; never commit it.

# Required before any request. The provider has to be able to tell who is
# asking, so this must contain a real contact address you control.
AUCTION_LENS_HTTP_USER_AGENT=

# Only needed if [reports.email] enabled = true in your configuration.
AUCTION_LENS_SMTP_HOST=
AUCTION_LENS_SMTP_USERNAME=
AUCTION_LENS_SMTP_PASSWORD=
AUCTION_LENS_EMAIL_FROM=
AUCTION_LENS_EMAIL_TO=

# Only needed if [reports.webhook] enabled = true. Treat it as a password:
# anyone holding this address can post into the channel.
AUCTION_LENS_WEBHOOK_URL=
"""


def setup(args: argparse.Namespace) -> int:
    """Create the two ignored files a fresh clone cannot carry, and say what to edit.

    Both are deliberately absent from git: one holds what a person wants, the
    other holds their secrets. A new machine therefore starts unable to run, and
    the only cure is a command that says so and fixes it.
    """
    config, env_file = Path(args.config), Path(args.env_file)
    print(_created(config, Path(EXAMPLE_CONFIG).read_text(encoding="utf-8")))
    print(_created(env_file, ENV_TEMPLATE))
    _report_required_edits(config, env_file)
    if args.email:
        return _ask_for_mail_settings(config, env_file)
    print()
    print(f"Then: {PROGRAM} daily")
    print(f"To be emailed the report: {PROGRAM} setup --email")
    return SUCCESS


def _ask_for_mail_settings(config: Path, env_file: Path) -> int:
    """Fill in the five mail variables, without the password ever being shown.

    The host is not allowlisted; its configured port and security mode still
    govern delivery. Provider-specific advice stays advice rather than turning
    this general setup command into a preference.
    """
    email = load_config(config).email
    _require_interactive_mail_setup()
    print()
    host = _answer("SMTP host", DEFAULT_SMTP_HOST)
    _mail_host_advice(host)
    username = _required_answer("SMTP username", "")
    sender_default = username if _looks_like_address(username) else ""
    sender = _address("From address", sender_default)
    recipient = _address("Address they are sent to", sender)
    password = _secret(
        "Password or app password", remove_display_spaces=_is_gmail_host(host)
    )

    write_settings(
        env_file,
        {
            email.host_env: host,
            email.username_env: username,
            email.password_env: password,
            email.sender_env: sender,
            email.recipient_env: recipient,
        },
    )
    print()
    print(f"[OK] {env_file} updated. The password was neither printed nor logged.")
    return _report_email_switch(config, email)


def _report_email_switch(config: Path, email: EmailConfig) -> int:
    """Read the switch with the real loader rather than guessing at the file.

    Programmatically rewriting arbitrary TOML risks corrupting the configuration
    it was meant to help with, so this reports the one line to change and leaves
    the file to its owner.
    """
    if email.enabled:
        print(f"[OK] {config} already has [reports.email] enabled = true.")
        print(f"     Transport is {email.security.value} on port {email.port}.")
        print()
        print(f"Check local readiness: {PROGRAM} doctor --email")
        print(f"Send one now: {PROGRAM} run --input {DEFAULT_INBOX} --email")
        return SUCCESS
    print(f"[!] {config} still has [reports.email] enabled = false.")
    print("    Mail settings were saved, but delivery is not ready.")
    print("    Set it to true and confirm that port and security suit your SMTP host.")
    print()
    print(f"Then check it: {PROGRAM} doctor --email")
    return SUCCESS


def _answer(question: str, default: str) -> str:
    """One line of input, where pressing Enter accepts the suggestion."""
    shown = f"{question} [{default}]: " if default else f"{question}: "
    return input(shown).strip() or default


def _required_answer(question: str, default: str) -> str:
    """A non-empty one-line answer, with an optional suggested value."""
    while True:
        answer = _answer(question, default)
        if answer:
            return answer
        print("    Nothing entered. Try again.")


def _address(question: str, default: str) -> str:
    """An email address, checked only for the shape every host agrees on."""
    while True:
        answer = _answer(question, default)
        if _looks_like_address(answer):
            return answer
        print("    That is not an email address. Try again.")


def _looks_like_address(value: str) -> bool:
    """Whether a value has the small amount of structure SMTP always needs."""
    return value.count("@") == 1 and all(part.strip() for part in value.split("@"))


def _secret(question: str, *, remove_display_spaces: bool) -> str:
    """Read a password without echoing it or silently changing provider data.

    Google prints an app password in four groups of four and people paste it
    exactly as shown. Only the Gmail host opts into removing that display
    formatting; another provider may legitimately use spaces in a password.
    """
    while True:
        with warnings.catch_warnings():
            warnings.simplefilter("error", GetPassWarning)
            try:
                typed = getpass(f"{question}: ")
            except GetPassWarning as error:
                raise RuntimeError(
                    "secure password input is unavailable in this terminal"
                ) from error
            except (EOFError, KeyboardInterrupt) as error:
                raise RuntimeError(
                    "mail setup stopped before a password was entered"
                ) from error
        if remove_display_spaces:
            typed = "".join(typed.split())
        if typed:
            return typed
        print("    Nothing entered. Try again.")


def _require_interactive_mail_setup() -> None:
    """Never fall back to a password prompt that may echo its input."""
    if not sys.stdin.isatty():
        raise RuntimeError(
            "mail setup needs an interactive terminal so the password stays hidden"
        )


def _mail_host_advice(host: str) -> None:
    """Say the one thing that host is known to need, without requiring it."""
    if _is_gmail_host(host):
        print("    Gmail needs 2-Step Verification and an app password, not the")
        print("    account password: https://myaccount.google.com/apppasswords")
        print("    See docs/GMAIL.md if that page offers you nothing.")


def _is_gmail_host(host: str) -> bool:
    """Identify the Gmail submission host narrowly enough to change a secret."""
    return host.strip().lower().rstrip(".") == DEFAULT_SMTP_HOST


def _created(path: Path, contents: str) -> str:
    """Write a starting file, and never overwrite one somebody has edited."""
    if path.exists():
        return f"[OK] {path} already exists, left alone"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents, encoding="utf-8")
    return f"[OK] wrote {path}"


def daily(args: argparse.Namespace) -> int:
    """Find lots, score them, and report what matters: the whole day in one word.

    Discovery and analysis stay separate commands because each is useful alone
    -- a parser can be corrected and re-run without asking the provider again --
    but nobody wants to type both every morning.
    """
    config = _with_todays_trips(load_config(args.config), args.visiting)
    _preflight_reports(config, args)
    discover(args)
    return run(argparse.Namespace(**{**vars(args), "input": args.output}))


def _with_todays_trips(config: AppConfig, visiting: list[str]) -> AppConfig:
    """Apply the errands already planned, which is a fact about today only.

    Deliberately a flag rather than a setting: it is true for one run and wrong
    by next week, and a saved answer to a question like this is one nobody
    remembers to change back.
    """
    if not visiting:
        return config
    return replace(config, locations=config.locations.already_visiting(tuple(visiting)))


def run(args: argparse.Namespace) -> int:
    """Score a listing file and print, and optionally email, the report."""
    config = _with_todays_trips(load_config(args.config), args.visiting)
    _preflight_reports(config, args)
    listings = load_listings(args.input)
    database = Database.at(args.database)
    database.initialize()

    result = analyze_listings(
        listings,
        config,
        observations=ObservationStore(database),
        decisions=LogisticsDecisionStore(database),
        watchlist=WatchlistStore(Path(args.watchlist)),
        valuation_engine=_valuation_engine(config),
    )
    print(render_text(result.candidates), end="")
    _report_skipped(result.listings_from_other_providers, config.provider.provider_id)
    _report_capped(result.matches_not_shown, len(result.candidates))
    _report_followed(result.lots_followed, args.watchlist)

    if args.email:
        send_email(result.candidates, config.email)
    if args.webhook:
        send_webhook(result.candidates, config.webhook)
        print(f"Posted {len(result.candidates)} match(es) to the webhook.")
    return SUCCESS


def _report_required_edits(config: Path, env_file: Path) -> None:
    """Name the local decisions no public repository can safely make."""
    print()
    print("Before the first run, edit:")
    print(
        f"  {env_file}: identify your requests with a contact address you control in "
        "AUCTION_LENS_HTTP_USER_AGENT"
    )
    print(f"  {config}: confirm your authorization, locations, and interests")


def doctor(args: argparse.Namespace) -> int:
    """Check a daily run's local prerequisites without changing state or using network."""
    config = load_config(args.config)
    if config.acquisition.run_mode != RunMode.PRODUCTION:
        raise RuntimeError(
            "scheduled runs require [provider.acquisition] run_mode = \"production\""
        )
    check_discovery_ready(config.provider, config.acquisition, _search_terms(config, []))
    print(f"[OK] {args.config}: discovery is configured and authorized.")

    requested = _doctor_destinations(config, args)
    _preflight_reports(config, argparse.Namespace(**requested))
    if requested["email"]:
        print("[OK] email is enabled and all configured environment values are present.")
    if requested["webhook"]:
        print("[OK] webhook is enabled and its configured HTTPS address is present.")
    if not any(requested.values()):
        print("[OK] no report destinations are enabled; local output only.")
    print("[OK] no network requests were made.")
    return SUCCESS


def _doctor_destinations(config: AppConfig, args: argparse.Namespace) -> dict[str, bool]:
    """Check explicitly requested channels, or every channel switched on in TOML."""
    if args.email or args.webhook:
        return {"email": args.email, "webhook": args.webhook}
    return {"email": config.email.enabled, "webhook": config.webhook.enabled}


def _preflight_reports(config: AppConfig, args: argparse.Namespace) -> None:
    """Resolve requested destinations before analysis can change local history."""
    if args.email:
        check_email_ready(config.email)
    if args.webhook:
        if not config.webhook.enabled:
            raise RuntimeError("webhook reporting is disabled in the selected configuration")
        webhook_address(config.webhook)


def fetch(args: argparse.Namespace) -> int:
    """Fetch one authorized page and report what happened to the cache."""
    config = load_config(args.config)
    result = fetch_authorized_page(config.provider, config.acquisition)
    provider = config.provider.display_name or config.provider.provider_id
    outcome = (
        "cached response reused"
        if result.reused_cache
        else f"{result.bytes_received} bytes cached"
    )
    print(f"{provider} returned HTTP {result.status}; {outcome} at {result.cache_path}")
    return SUCCESS


def discover(args: argparse.Namespace) -> int:
    """Ask the provider's search for lots, and write what it lists."""
    config = load_config(args.config)
    captures = discover_searches(
        config.provider, config.acquisition, _search_terms(config, args.search)
    )

    found = []
    for capture in captures:
        listed = read_search_page(
            capture.path.read_text(encoding="utf-8", errors="replace"),
            source=config.provider.provider_id,
            page_url=capture.url,
        )
        found.extend(listed)
        state = "unchanged" if capture.reused_cache else "fetched"
        print(f"  {capture.term}: {len(listed)} lot(s) ({state})")

    rows = unique_lots(found)
    write_json_atomically(Path(args.output), {LISTINGS_KEY: rows})
    print(f"Found {len(rows)} lot(s) from {len(captures)} page(s) into {args.output}.")
    return SUCCESS


def _search_terms(config: AppConfig, requested: list[str]) -> list[str]:
    """What to search for: what was asked, what was configured, or what is wanted.

    Falling back to the interest rules means the terms are written down once. A
    configuration that already says it wants a soundbar does not have to say so
    again in a second list.
    """
    if requested:
        return requested
    if config.acquisition.searches:
        return list(config.acquisition.searches)
    return [term for rule in config.interests for term in rule.any_terms]


def pull(args: argparse.Namespace) -> int:
    """Read saved provider pages into the canonical file that `run` analyses."""
    config = load_config(args.config)
    pages = _saved_pages(Path(args.input))
    rows, failures = [], []
    for page in pages:
        try:
            rows.extend(
                read_saved_page(
                    page.read_text(encoding="utf-8", errors="replace"),
                    source=config.provider.provider_id,
                    page_url=_saved_page_url(page),
                )
            )
        except ValueError as error:
            # One page the provider changed must not lose the other fifty.
            failures.append(f"{page.name}: {error}")

    lots = unique_lots(rows)
    write_json_atomically(Path(args.output), {LISTINGS_KEY: lots})
    print(f"Read {len(lots)} lot(s) from {len(pages)} saved page(s) into {args.output}.")
    for failure in failures:
        print(f"  [!] {failure}")
    return SUCCESS


def logistics(args: argparse.Namespace) -> int:
    """Save or clear one listing's handling decision."""
    database = Database.at(args.database)
    database.initialize()
    decisions = LogisticsDecisionStore(database)

    if args.status == CLEAR:
        decisions.clear(args.source, args.listing_id)
        print("Logistics decision cleared.")
        return SUCCESS

    decision = LogisticsDecision(
        status=LogisticsStatus(args.status),
        added_cost=parse_money(args.added_cost, field_name="added_cost"),
        note=args.note.strip(),
    )
    decisions.save(args.source, args.listing_id, decision)
    print(
        f"Logistics decision saved as {decision.status} "
        f"with ${decision.added_cost} added cost."
    )
    return SUCCESS


def watch(args: argparse.Namespace) -> int:
    """Record what a person thinks of one lot, or stop following it."""
    store = WatchlistStore(Path(args.watchlist))
    if args.verdict == DROP:
        removed = store.drop(args.source, args.listing_id)
        print("Stopped following." if removed else "That lot was not being followed.")
        return SUCCESS

    followed = store.get(args.source, args.listing_id) or WatchedItem(
        source=args.source, listing_id=args.listing_id
    )
    updated = replace(followed, **_stated_opinions(args))
    store.save(updated)
    print(f"{updated.uid}: {updated.verdict}.")
    return SUCCESS


def watchlist(args: argparse.Namespace) -> int:
    """Show the followed lots, keenest first."""
    items = WatchlistStore(Path(args.watchlist)).items()
    if args.verdict:
        items = tuple(item for item in items if item.verdict == args.verdict)
    # Colour only when a person is watching; a redirected list stays plain.
    print(
        render_watchlist(items, path=args.watchlist, colour=sys.stdout.isatty()),
        end="",
    )
    if args.email:
        if not items:
            print("No selected lots; no email sent.")
            return SUCCESS
        config = load_config(args.config)
        check_email_ready(config.email)
        send_watchlist_email(items, config.email)
        print(f"Emailed {len(items)} selected lot(s).")
    return SUCCESS


def _stated_opinions(args: argparse.Namespace) -> dict:
    """Change only the fields the person actually named on the command line.

    An unnamed field keeps whatever the file already said, so adding one star
    never silently erases the estimate written last week.
    """
    changes = {}
    if args.verdict is not None:
        changes["verdict"] = args.verdict
    if args.estimate is not None:
        changes["my_estimate"] = parse_money(args.estimate, field_name="estimate")
    if args.note is not None:
        changes["note"] = args.note.strip()
    return changes


def _saved_page_url(page: Path) -> str:
    """The address a saved page came from, recorded beside it when it was cached.

    A search page describes many lots and links each one relatively, so the
    address it was fetched from is what turns those links back into real ones.
    """
    metadata = read_json(page.with_suffix(page.suffix + METADATA_SUFFIX), default={})
    return str(metadata.get("source_url", ""))


def _saved_pages(source: Path) -> list[Path]:
    """Accept one page or a directory of them, so a batch is not a special case."""
    if source.is_dir():
        return sorted(source.glob(f"*{PAGE_SUFFIX}"))
    if not source.is_file():
        raise ValueError(f"{source} is not a saved page or a directory of them")
    return [source]


def _valuation_engine(config: AppConfig) -> ValuationEngine | None:
    """Value listings only when the configuration asked for it."""
    return ValuationEngine(config.valuation) if config.valuation.enabled else None


def _report_skipped(count: int, provider_id: str) -> None:
    """Say so when input was ignored, rather than silently dropping listings."""
    if count:
        print(f"Ignored {count} listing(s) from other providers than {provider_id}.")


def _report_capped(hidden: int, shown: int) -> None:
    """Never hide part of the ranking quietly.

    A cap the reader has forgotten about looks exactly like a quiet day, and
    the difference between "nothing was out there" and "you asked for less"
    matters enough to spend a line on.
    """
    if hidden:
        print(
            f"Showing the best {shown}; {hidden} more matched. "
            "Raise reports.max_items to see them."
        )


def _report_followed(count: int, path: str) -> None:
    """Say where the price readings went, so the file is never a surprise."""
    if count:
        print(f"Recorded a price reading for {count} lot(s) in {path}.")
