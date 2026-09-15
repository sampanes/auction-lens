"""Check everything a scheduled run needs before it can fail unattended.

Deliberately offline and deliberately read-only. Its whole value is that it can
be run at any moment without consequence, so a scheduler can call it first and
refuse to go on.
"""

from __future__ import annotations

import argparse

from .config.app import AppConfig
from .config.load import load_config
from .config.provider import RunMode
from .providers.nellis.discover import check_discovery_ready
from .providers.search_terms import search_terms
from .reports.send import preflight_reports


def doctor(args: argparse.Namespace) -> int:
    """Check a daily run's local prerequisites without changing state or using network."""
    config = load_config(args.config)
    if config.acquisition.run_mode != RunMode.PRODUCTION:
        raise RuntimeError(
            "scheduled runs require [provider.acquisition] run_mode = \"production\""
        )
    check_discovery_ready(config.provider, config.acquisition, search_terms(config, []))
    print(f"[OK] {args.config}: discovery is configured and authorized.")

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
