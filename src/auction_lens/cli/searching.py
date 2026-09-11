"""Which phrases a run asks the provider for.

Three commands need this answer and none of them should hold its own opinion
about it, or a search typed on the command line would mean one thing to
``discover`` and another to ``doctor``.
"""

from __future__ import annotations

from ..config import AppConfig, InterestRule


def search_terms(config: AppConfig, requested: list[str]) -> list[str]:
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


def daily_search_terms(
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
        return search_terms(config, requested)
    return _interest_search_terms(active_interests)


def _interest_search_terms(interests: tuple[InterestRule, ...]) -> list[str]:
    """Flatten configured phrases in rule order; discovery owns deduping and caps."""
    return [term for rule in interests for term in rule.any_terms]
