"""Resolution of condition policies from reusable profiles plus local overrides.

An operator writes one ``[condition_profiles.name]`` table and points several
interests at it, so this is the only place that knows how a profile and a
rule-local ``condition`` table combine.
"""

from __future__ import annotations

from .schema import ConditionPolicy
from .toml_reader import Section

PROFILE_KEY = "condition_profile"
INLINE_KEY = "condition"


def resolve_condition_policy(
    owner: Section,
    profiles: Section,
    *,
    profile_key: str = PROFILE_KEY,
    inline_key: str = INLINE_KEY,
) -> ConditionPolicy:
    """Start from the named profile, then let the owner's own table override it.

    Rejections and ``allow_unknown`` replace the profile value outright, while
    penalties merge, so a rule can retune one label without restating a profile.
    """
    profile = _named_profile(owner, profiles, profile_key)
    inline = owner.table(inline_key)

    penalties = profile.non_negative_integer_map("penalties")
    penalties.update(inline.non_negative_integer_map("penalties"))
    rejected = inline if inline.contains("reject") else profile
    unknown_owner = inline if inline.contains("allow_unknown") else profile
    return ConditionPolicy(
        reject=frozenset(rejected.lowercase_texts("reject")),
        penalties=penalties,
        allow_unknown=unknown_owner.flag("allow_unknown", True),
    )


def _named_profile(owner: Section, profiles: Section, profile_key: str) -> Section:
    name = owner.text(profile_key).strip()
    if not name:
        return Section({})
    if not profiles.contains(name):
        raise ValueError(f"unknown condition profile {name!r}")
    return profiles.table(name)
