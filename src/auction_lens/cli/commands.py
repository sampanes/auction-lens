"""One function per command, each doing only what its name says."""

from __future__ import annotations

import argparse
import sys
import warnings
from dataclasses import replace
from datetime import UTC, datetime
from getpass import GetPassWarning, getpass
from pathlib import Path

from ..acquisition import (
    METADATA_SUFFIX,
    ResponseCache,
    check_discovery_ready,
    discover_searches,
    fetch_authorized_page,
)
from ..config import (
    AppConfig,
    EmailConfig,
    InterestRule,
    RunMode,
    load_config,
    render_profile,
)
from ..env_file import write_settings
from ..fields import parse_money
from ..file_io import read_json, write_json_atomically
from ..ingest import dated, load_listings, read_saved_page, read_search_page, unique_lots
from ..models import (
    Candidate,
    InterestRef,
    LogisticsDecision,
    LogisticsStatus,
    Verdict,
    WatchedItem,
    harvest_of,
)
from ..notifications import (
    DeliveryChannel,
    DeliveryRoute,
    ReportKind,
    candidate_items,
    outcome_fingerprint,
    plan_candidates,
    plan_watchlist,
    watchlist_items,
)
from ..outcomes import plan_interests
from ..pipeline import RunResult, analyze_listings, follow_candidates
from ..reporting import (
    DeliverySummary,
    destination_fingerprint,
    email_destination,
    render_closing_prices,
    render_text,
    render_watchlist,
    send_email,
    send_watchlist_email,
    send_webhook,
    webhook_destination,
)
from ..reporting.webhook import webhook_item_limit
from ..storage import (
    ClosingPriceStore,
    Database,
    DeliveryLedger,
    LogisticsDecisionStore,
    ObservationStore,
    WatchlistStore,
)
from ..text_match import mentions
from ..valuation import ValuationEngine
from .parser import CLEAR, DEFAULT_CONFIG, DEFAULT_INBOX, DROP, EXAMPLE_CONFIG, PROGRAM
from .profile_wizard import edit_profile, restore_profile

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
    print()
    if config.suffix == ".toml":
        print(f"Review or change practical limits: {_profile_editor_command(config)}")
    else:
        print("Guided profile editing requires a configuration ending in .toml.")
    if args.email:
        return _ask_for_mail_settings(config, env_file)
    print(f"Then: {PROGRAM} daily")
    print(f"To be emailed the report: {PROGRAM} setup --email")
    return SUCCESS


def profile(args: argparse.Namespace) -> int:
    """Explain the stable operator choices without consulting any runtime state."""
    if args.edit:
        edit_profile(args.config)
        return SUCCESS
    if args.restore:
        restore_profile(args.config)
        return SUCCESS
    print(render_profile(load_config(args.config)), end="")
    return SUCCESS


def _profile_editor_command(config: Path) -> str:
    if config == Path(DEFAULT_CONFIG):
        return f"{PROGRAM} profile --edit"
    return f'{PROGRAM} profile --config "{config}" --edit'


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
    destinations = _preflight_reports(config, args)
    watchlist = WatchlistStore(Path(args.watchlist))
    plan = plan_interests(config.interests, watchlist.items())
    terms = _daily_search_terms(config, args.search, plan.active_rules)
    if (
        not terms
        and not config.acquisition.categories
        and config.interests
        and not plan.active_rules
    ):
        _write_satisfied_discovery(args.output)
    else:
        _discover(args, config, terms)
    run_args = argparse.Namespace(**{**vars(args), "input": args.output})
    return _run(run_args, config, watchlist, destinations)


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
    destinations = _preflight_reports(config, args)
    return _run(args, config, WatchlistStore(Path(args.watchlist)), destinations)


def _run(
    args: argparse.Namespace,
    config: AppConfig,
    watchlist: WatchlistStore,
    destinations: dict[DeliveryChannel, str],
) -> int:
    """Execute an already-loaded run, so ``daily`` need not load its inputs twice."""
    listings = load_listings(args.input)
    database = Database.at(args.database)
    database.initialize()

    result = analyze_listings(
        listings,
        config,
        observations=ObservationStore(database),
        decisions=LogisticsDecisionStore(database),
        watchlist=watchlist,
        valuation_engine=_valuation_engine(config),
    )
    zone = config.acquisition.zone
    searches = result.searches
    order = config.reports.order
    print(
        render_text(
            result.candidates,
            zone,
            searches,
            order,
            result.interest_progress,
            result.unreviewed_wins,
            harvest=result.harvest,
        ),
        end="",
    )
    _report_skipped(result.listings_from_other_providers, config.provider.provider_id)
    _report_outside_window(
        result.lots_outside_the_window, config.reports.closing_within_hours
    )
    _report_capped(result.matches_not_shown, len(result.candidates))
    additionally_followed, delivery_failures = _deliver_findings(
        args,
        config,
        result,
        watchlist,
        destinations,
    )
    _report_followed(result.lots_followed + additionally_followed, args.watchlist)
    if delivery_failures:
        raise RuntimeError("; ".join(delivery_failures))
    return SUCCESS


def _deliver_findings(
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
            failures.append(_delivery_failure(channel, accepted, phase, error))
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
    if channel == DeliveryChannel.EMAIL:
        send_email(
            selected,
            config.email,
            config.acquisition.zone,
            result.searches,
            config.reports.order,
            result.interest_progress,
            result.unreviewed_wins,
            delivery,
            # Counted against what this destination is actually being sent,
            # so a suppressed lot is not described as one still on the page.
            harvest_of(list(result.all_candidates), selected),
        )
        return
    send_webhook(
        selected,
        config.webhook,
        config.acquisition.zone,
        result.interest_progress,
        result.unreviewed_wins,
        delivery,
        order=config.reports.order,
    )


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
    destinations = _preflight_reports(
        config,
        argparse.Namespace(
            **requested,
            repeat_delivery=False,
            delivery_ledger=args.delivery_ledger,
        ),
    )
    if requested["email"]:
        print("[OK] email is enabled and all configured environment values are present.")
    if requested["webhook"]:
        print("[OK] webhook is enabled and its configured HTTPS address is present.")
    if destinations:
        print(f"[OK] {args.delivery_ledger}: delivery receipts are ready.")
    if not any(requested.values()):
        print("[OK] no report destinations are enabled; local output only.")
    print("[OK] no network requests were made.")
    return SUCCESS


def _doctor_destinations(config: AppConfig, args: argparse.Namespace) -> dict[str, bool]:
    """Check explicitly requested channels, or every channel switched on in TOML."""
    if args.email or args.webhook:
        return {"email": args.email, "webhook": args.webhook}
    return {"email": config.email.enabled, "webhook": config.webhook.enabled}


def _preflight_reports(
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
    return _discover(args, config, _search_terms(config, args.search))


def _discover(args: argparse.Namespace, config: AppConfig, terms: list[str]) -> int:
    """Execute discovery with terms chosen by the calling workflow."""
    captures = discover_searches(config.provider, config.acquisition, terms)

    found = []
    for capture in captures:
        listed = read_search_page(
            capture.path.read_text(encoding="utf-8", errors="replace"),
            source=config.provider.provider_id,
            page_url=capture.url,
        )
        # The page's own download time, not now: a page reused from the cache
        # describes prices that were true when it was fetched.
        found.extend(dated(listed, capture.fetched_at))
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
    return _interest_search_terms(config.interests)


def _daily_search_terms(
    config: AppConfig,
    requested: list[str],
    active_interests: tuple[InterestRule, ...],
) -> list[str]:
    """Choose daily fallbacks from wants that recorded outcomes have not filled.

    Direct command-line and configured searches are deliberate acquisition
    instructions, so neither is filtered through the outcome history. Only the
    convenience fallback follows finite-interest retirement.
    """
    if requested or config.acquisition.searches:
        return _search_terms(config, requested)
    return _interest_search_terms(active_interests)


def _write_satisfied_discovery(output: str) -> None:
    """Complete a quiet daily run when every configured want is already filled."""
    write_json_atomically(Path(output), {LISTINGS_KEY: []})
    print("All finite interests are satisfied; no provider request was needed.")


def _interest_search_terms(interests: tuple[InterestRule, ...]) -> list[str]:
    """Flatten configured phrases in rule order; discovery owns deduping and caps."""
    return [term for rule in interests for term in rule.any_terms]


def pull(args: argparse.Namespace) -> int:
    """Read saved provider pages into the canonical file that `run` analyses."""
    config = load_config(args.config)
    pages = _saved_pages(Path(args.input))
    rows, failures = [], []
    for page in pages:
        try:
            listed = read_saved_page(
                page.read_text(encoding="utf-8", errors="replace"),
                source=config.provider.provider_id,
                page_url=_saved_page_url(page),
            )
            # A saved page can be read weeks after it was saved, so the lots in
            # it keep the date of the page rather than the date of this run.
            rows.extend(dated(listed, _saved_page_time(page)))
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
    source, listing_id = _watch_identity(args)
    store = WatchlistStore(Path(args.watchlist))
    if args.verdict == DROP:
        if args.fulfills is not None or args.clear_fulfillments:
            raise ValueError("drop cannot be combined with fulfillment changes")
        followed = store.get(source, listing_id)
        if followed is not None and followed.fulfilled_interests:
            names = ", ".join(
                reference.name for reference in followed.fulfilled_interests
            )
            raise ValueError(
                f"cannot drop {followed.uid} while it fulfills: {names}; "
                "first run watch with --clear-fulfillments (this reopens the "
                "interest), then run watch with --verdict drop"
            )
        removed = store.drop(source, listing_id)
        print("Stopped following." if removed else "That lot was not being followed.")
        return SUCCESS

    followed = store.get(source, listing_id) or WatchedItem(
        source=source, listing_id=listing_id
    )
    changes = _stated_opinions(args)
    resulting_verdict = Verdict(changes.get("verdict", followed.verdict))
    if args.fulfills is not None:
        if resulting_verdict != Verdict.WON:
            raise ValueError(
                "--fulfills requires the resulting verdict to be won; "
                "add --verdict won"
            )
        changes["fulfilled_interests"] = _resolve_fulfillments(
            args.fulfills, followed.matched_interests
        )
        changes["fulfillment_reviewed"] = True
    elif args.clear_fulfillments:
        changes["fulfilled_interests"] = ()
        changes["fulfillment_reviewed"] = True

    updated = replace(followed, **changes)
    store.save(updated)
    print(_watch_confirmation(updated))
    return SUCCESS


def _watch_identity(args: argparse.Namespace) -> tuple[str, str]:
    """Read either the report's copyable key or the older two-flag spelling."""
    if args.key:
        if args.source or args.listing_id:
            raise ValueError("--key cannot be combined with --source or --listing-id")
        source, separator, listing_id = args.key.strip().partition("/")
        if not separator or not source or not listing_id:
            raise ValueError("--key must be the SOURCE/LISTING-ID shown in the report")
        return source, listing_id
    if not args.source or not args.listing_id:
        raise ValueError("use --key SOURCE/LISTING-ID, or both --source and --listing-id")
    return args.source, args.listing_id


def sold(args: argparse.Namespace) -> int:
    """Show what closed lots were last seen going for, tightest reading first."""
    config = load_config(args.config)
    database = Database.at(args.database)
    database.initialize()
    prices = ClosingPriceStore(database).closed_by(datetime.now(UTC))
    if args.match:
        prices = tuple(
            price for price in prices if mentions(price.title.lower(), args.match.lower())
        )
    print(
        render_closing_prices(
            prices,
            config.acquisition.zone,
            within_minutes=args.within_minutes,
            limit=args.limit,
        ),
        end="",
    )
    return SUCCESS


def watchlist(args: argparse.Namespace) -> int:
    """Show the followed lots, keenest first."""
    if args.repeat_delivery and not args.email:
        raise ValueError("--repeat-delivery requires --email")
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
        destinations = _preflight_reports(config, args)
        destination = destinations[DeliveryChannel.EMAIL]
        # A filtered watchlist is a different recurring report from the full
        # list. Hashing the already-opaque destination with the public selector
        # keeps those receipt streams separate without retaining either value.
        selector = "all" if args.verdict is None else str(args.verdict)
        route_fingerprint = destination_fingerprint(
            f"{destination}\0watchlist-selection={selector}"
        )
        route = DeliveryRoute(
            ReportKind.WATCHLIST,
            DeliveryChannel.EMAIL,
            route_fingerprint,
        )
        accepted = False
        phase = "receipt planning"
        try:
            with DeliveryLedger(Path(args.delivery_ledger)).session(route) as delivery:
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
                    return SUCCESS
                phase = "transport"
                send_watchlist_email(
                    plan.items,
                    config.email,
                    DeliverySummary(
                        active=True,
                        repeated=args.repeat_delivery,
                        unchanged_matches=plan.unchanged_items,
                        item_singular="selected lot",
                        item_plural="selected lots",
                    ),
                )
                accepted = True
                phase = "local receipt"
                delivery.accept(plan.receipts, outcome_fingerprint((), 0))
            print(f"Emailed {len(plan.items)} selected lot(s).")
        except (OSError, RuntimeError, ValueError) as error:
            raise RuntimeError(
                _delivery_failure(
                    DeliveryChannel.EMAIL,
                    accepted,
                    phase,
                    error,
                )
            ) from error
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


def _resolve_fulfillments(
    requested: list[str], recorded: tuple[InterestRef, ...]
) -> tuple[InterestRef, ...]:
    """Resolve human-friendly names without making renamed config a dependency.

    The recorded matches are the authority for this historical lot. Stable ids
    win over names so an unfortunate display-name collision can never make an
    id unusable; display names must identify exactly one recorded match.
    """
    resolved = []
    for entered in requested:
        key = entered.strip().casefold()
        id_match = next(
            (ref for ref in recorded if ref.interest_id.casefold() == key), None
        )
        if id_match is not None:
            chosen = id_match
        else:
            name_matches = [ref for ref in recorded if ref.name.casefold() == key]
            if len(name_matches) != 1:
                problem = "is ambiguous" if name_matches else "is not a recorded match"
                raise ValueError(
                    f"cannot use {entered!r} for --fulfills: it {problem}. "
                    f"Recorded matches: {_recorded_matches(recorded)}"
                )
            chosen = name_matches[0]
        if all(
            existing.interest_id.casefold() != chosen.interest_id.casefold()
            for existing in resolved
        ):
            resolved.append(chosen)
    return tuple(resolved)


def _recorded_matches(references: tuple[InterestRef, ...]) -> str:
    """A copyable list for a correction after an unknown or ambiguous name."""
    if not references:
        return "none (run this listing through Auction Lens before assigning it)"
    return ", ".join(
        f"{reference.interest_id} ({reference.name})" for reference in references
    )


def _watch_confirmation(item: WatchedItem) -> str:
    """Confirm whether a saved allocation currently counts toward a want."""
    opening = f"{item.uid}: {item.verdict}."
    if item.verdict == Verdict.WON:
        if item.fulfilled_interests:
            names = ", ".join(ref.name for ref in item.fulfilled_interests)
            return f"{opening} Fulfills: {names}."
        if item.fulfillment_reviewed:
            return f"{opening} Fulfillment reviewed: fulfills none."
        if item.matched_interests:
            return (
                f"{opening} Fulfillment unreviewed; use --fulfills INTEREST "
                "to assign it, or --clear-fulfillments if it fulfilled none."
            )
        return f"{opening} Fulfillment unreviewed; no matches were recorded."
    if item.fulfilled_interests:
        return f"{opening} Saved fulfillment is inactive until the verdict is won."
    if item.fulfillment_reviewed:
        return f"{opening} Fulfillment reviewed: fulfills none."
    return opening


def _saved_page_url(page: Path) -> str:
    """The address a saved page came from, recorded beside it when it was cached.

    A search page describes many lots and links each one relatively, so the
    address it was fetched from is what turns those links back into real ones.
    """
    metadata = read_json(page.with_suffix(page.suffix + METADATA_SUFFIX), default={})
    return str(metadata.get("source_url", ""))


def _saved_page_time(page: Path) -> datetime:
    """When a saved page was downloaded, which is when its prices were true.

    Pages saved before that was recorded have nothing better to offer than the
    file's own modification time, which is what writing it set.
    """
    recorded = ResponseCache.at(page).fetched_at()
    if recorded is not None:
        return recorded
    return datetime.fromtimestamp(page.stat().st_mtime, UTC)


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


def _report_outside_window(count: int, within_hours: int | None) -> None:
    """Say how many lots were set aside for closing too late, or not at all.

    Without the window that is only the lots that already closed, which needs
    no explanation. With one it is a choice the reader made and may want back,
    so the line names the setting that made it.
    """
    if not count:
        return
    if within_hours is None:
        print(f"Passed over {count} lot(s) that have already closed.")
        return
    print(
        f"Passed over {count} lot(s) already closed or closing more than "
        f"{within_hours}h out. Change reports.closing_within_hours to widen it."
    )


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
    """Say where followed-lot history changed, so the file is never a surprise."""
    if count:
        print(f"Updated {count} followed lot(s) in {path}.")
