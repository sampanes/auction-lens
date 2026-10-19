"""Check everything a scheduled run needs before it can fail unattended.

Deliberately offline and deliberately read-only. Its whole value is that it can
be run at any moment without consequence, so a scheduler can call it first and
refuse to go on.
"""

from __future__ import annotations

import argparse

from .config.app import AppConfig
from .config.load import load_config
from .config.provider import AcquisitionConfig, RunMode
from .providers.registry import resolve_provider
from .providers.search_terms import distinct_terms, search_terms
from .reports.send import preflight_reports


def doctor(args: argparse.Namespace) -> int:
    """Check a daily run's local prerequisites without changing state or using network."""
    config = load_config(args.config)
    adapter = resolve_provider(config.provider.provider_id)
    _check_acquisition(config, adapter, args.config)

    requested = _destinations(config, args)
    destinations = preflight_reports(
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
    _check_judging(config)
    print("[OK] no network requests were made.")
    return 0


def _check_acquisition(config: AppConfig, adapter, name: str) -> None:
    """Check how this run gets its lots, which is not the same for every provider.

    A provider that cannot be asked for lots has nothing to authorize and no
    pacing to respect, so the production run mode and the readiness check both
    have nothing to say about it. Refusing here anyway would make this command
    useless to the one provider whose run is entirely unattended: its records
    arrive from a browser companion, and the mail settings below are exactly
    what such a run fails on at seven in the morning.

    The refusal still stands where it means something. Nothing in this branch
    makes the program able to fetch; it only stops a preflight from reporting
    the absence of a fetch as a fault.
    """
    if not adapter.discovers:
        print(f"[OK] {name}: this provider is read from records; nothing here fetches.")
        return
    if config.acquisition.run_mode != RunMode.PRODUCTION:
        raise RuntimeError(
            "scheduled runs require [provider.acquisition] run_mode = \"production\""
        )
    adapter.check_discovery_ready(
        config.provider, config.acquisition, search_terms(config, [])
    )
    _check_run_caps(config.acquisition, search_terms(config, []))
    print(f"[OK] {name}: discovery is configured and authorized.")


def _check_run_caps(acquisition: AcquisitionConfig, terms: list[str]) -> None:
    """Name anything configured that a run's own caps will never ask for.

    Each cap takes the first entries in order rather than taking turns, so an
    entry past one is never requested on any run. That is deliberate, and it is
    also invisible: the list and its cap are naturally set to the same number
    by an operator who means "ask for all of these", and the next phrase added
    then retires the one at the bottom without a word. This is a notice rather
    than a fault, because a long list against a small cap is a legitimate
    arrangement -- the caps exist so that terms cannot starve the sweep.
    """
    for label, configured, cap in (
        ("search term", distinct_terms(terms), acquisition.max_searches_per_run),
        (
            "category",
            distinct_terms(acquisition.categories),
            acquisition.max_categories_per_run,
        ),
    ):
        ignored = configured[cap:]
        if ignored:
            print(
                f"[!] {len(ignored)} {label}(s) sit past the per-run cap of {cap} "
                f"and are never asked for: {', '.join(ignored)}"
            )


def _check_judging(config: AppConfig) -> None:
    """Name any interest that would go unjudged, without contacting anything.

    An interest with no sentence is not an error -- it simply passes through
    unvetted, exactly as it did before there was a judge. But it is now the
    mistake worth catching: a rule added without one is silently the only
    rule in the file still deciding by word match alone.

    The endpoint is deliberately not contacted. This command promises to make
    no network requests, and a reachability check belongs to the run that
    depends on it, which already survives an unreachable judge.
    """
    if not config.judging.enabled:
        print("[OK] judging is off; interests are matched on their terms alone.")
        return
    unwritten = [rule.name for rule in config.interests if not rule.wants.strip()]
    if unwritten:
        print(f"[!] no 'wants' sentence, so never vetted: {', '.join(unwritten)}")
    else:
        print(f"[OK] all {len(config.interests)} interests have a 'wants' sentence.")
    print(f"[OK] judging will ask {config.judging.model} at {config.judging.endpoint}.")


def _destinations(config: AppConfig, args: argparse.Namespace) -> dict[str, bool]:
    """Check explicitly requested channels, or every channel switched on in TOML."""
    if args.email or args.webhook:
        return {"email": args.email, "webhook": args.webhook}
    return {"email": config.email.enabled, "webhook": config.webhook.enabled}
