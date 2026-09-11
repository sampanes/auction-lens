"""Check everything a scheduled run needs, before it can fail unattended at 06:00.

Deliberately offline and deliberately read-only. Its whole value is that it can
be run at any moment without consequence, so a scheduler can call it first and
refuse to go on.
"""

from __future__ import annotations

import argparse

from ..acquisition import check_discovery_ready
from ..config import AppConfig, RunMode, load_config
from .exit_codes import SUCCESS
from .searching import search_terms
from .sending import preflight_reports


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
    print("[OK] no network requests were made.")
    return SUCCESS


def _destinations(config: AppConfig, args: argparse.Namespace) -> dict[str, bool]:
    """Check explicitly requested channels, or every channel switched on in TOML."""
    if args.email or args.webhook:
        return {"email": args.email, "webhook": args.webhook}
    return {"email": config.email.enabled, "webhook": config.webhook.enabled}
