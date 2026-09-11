"""The configuration a TOML file describes, as immutable typed records.

Each record mirrors one concern in the file, so a consumer can depend on the
narrow slice it actually needs: cost estimation takes economics, the fetcher
takes acquisition, and only the CLI assembles the whole application config.

A record enforces its own rules in ``__post_init__``. That is what lets every
consumer downstream simply use a value instead of re-checking it, and it is why
the loader in this package is nothing but field mapping. Where a setting may
only be one of a few words, it is an enum, so a typo is caught by construction
rather than by a membership test repeated at each use.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from decimal import Decimal
from enum import StrEnum
from typing import Any, TypeVar
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..fields import (
    require_at_least,
    require_at_most,
    require_finite,
    require_not_negative,
    require_within,
)
from ..models import (
    HIGHEST_INTEREST_SCORE,
    HIGHEST_SCORE,
    LOWEST_SCORE,
    ReadingOrder,
)

DEFAULT_USER_AGENT_ENV = "AUCTION_LENS_HTTP_USER_AGENT"
DEFAULT_CACHE_FILE = "private/cache/provider-response.html"
DEFAULT_SEARCH_CACHE_DIR = "private/cache/searches"
DEFAULT_LEDGER_FILE = "private/poll-ledger.json"

HIGHEST_PORT = 65535

# Where a search term is written into the search address.
QUERY_PLACEHOLDER = "{query}"
CATEGORY_PLACEHOLDER = "{category}"

# A rate is a proportion, so anything above 1 is a misplaced percentage.
HIGHEST_RATE = Decimal("1")

# Source ids name cache files and report rows, so they stay filesystem-safe.
SOURCE_ID_CHARACTERS = "letters, digits, and _ . -"

Choice = TypeVar("Choice", bound=StrEnum)


class LargeItemPolicy(StrEnum):
    """What to do about a lot too heavy or too bulky to carry casually."""

    ASK = "ask"
    ALLOW = "allow"
    REJECT = "reject"


class RunMode(StrEnum):
    """Whether a run is the real daily poll or someone iterating on a parser."""

    PRODUCTION = "production"
    DEVELOPMENT = "development"


class EmailSecurity(StrEnum):
    """How the SMTP connection is protected."""

    SSL = "ssl"
    STARTTLS = "starttls"


class AcquisitionMode(StrEnum):
    """Where listing data comes from."""

    MANUAL = "manual"
    AUTHORIZED_HTTP = "authorized_http"


@dataclass(frozen=True)
class ProviderConfig:
    """Which auction site a configuration file describes."""

    provider_id: str
    display_name: str = ""
    enabled: bool = False


@dataclass(frozen=True)
class EconomicsConfig:
    """The fees that turn a winning bid into the amount actually paid."""

    default_buyer_premium: Decimal = Decimal("0")
    premium_is_taxable: bool = True
    sales_tax_rate: Decimal = Decimal("0")
    processing_fee: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        _require_rate(self.default_buyer_premium, field_name="default_buyer_premium")
        _require_rate(self.sales_tax_rate, field_name="sales_tax_rate")
        require_not_negative(self.processing_fee, field_name="processing_fee")


@dataclass(frozen=True)
class AcquisitionConfig:
    """How, and how often, the fetcher may contact the provider."""

    mode: AcquisitionMode = AcquisitionMode.MANUAL
    # Choosing authorized_http describes the transport. This separate switch
    # records the operator's affirmative decision that they actually have
    # permission to use it; copied public configuration must fail closed.
    authorization_confirmed: bool = False
    url: str = ""
    user_agent_env: str = DEFAULT_USER_AGENT_ENV
    timezone: str = "UTC"
    max_requests_per_day: int = 1
    minimum_interval_minutes: int = 720
    timeout_seconds: int = 30
    cache_file: str = DEFAULT_CACHE_FILE
    ledger_file: str = DEFAULT_LEDGER_FILE
    run_mode: RunMode = RunMode.PRODUCTION
    development_minimum_interval_seconds: int = 2
    search_url_template: str = ""
    searches: tuple[str, ...] = ()
    search_cache_dir: str = DEFAULT_SEARCH_CACHE_DIR
    max_searches_per_run: int = 8
    # A sweep answers what a search term cannot: what is here that I would
    # want but would never have thought to type. Capped separately from the
    # searches so a long list of terms can never starve it.
    category_url_template: str = ""
    categories: tuple[str, ...] = ()
    max_categories_per_run: int = 12
    seconds_between_searches: Decimal = Decimal("5")
    session_url: str = ""
    session_fields: dict[str, str] = field(default_factory=dict)
    # A POST that establishes branch-scoping session state can fall outside
    # permission to poll public pages, so it needs its own confirmation.
    session_change_authorized: bool = False

    @property
    def zone(self) -> ZoneInfo:
        """The provider's own clock, which every local time here is read against.

        A lot closes at the auction house, not where the reader happens to be
        standing, so the same zone that decides which day a request quota falls
        in also decides what time a report says a lot ends. Built here rather
        than by each caller so there is one answer to what "local" means.
        """
        return ZoneInfo(self.timezone)

    def __post_init__(self) -> None:
        _settle(self, "mode", AcquisitionMode)
        _settle(self, "run_mode", RunMode)
        require_at_least(self.max_requests_per_day, 1, field_name="max_requests_per_day")
        require_at_least(self.timeout_seconds, 1, field_name="timeout_seconds")
        require_not_negative(
            self.minimum_interval_minutes, field_name="minimum_interval_minutes"
        )
        require_not_negative(
            self.development_minimum_interval_seconds,
            field_name="development_minimum_interval_seconds",
        )
        require_at_least(self.max_searches_per_run, 1, field_name="max_searches_per_run")
        require_at_least(
            self.max_categories_per_run, 1, field_name="max_categories_per_run"
        )
        require_not_negative(
            self.seconds_between_searches, field_name="seconds_between_searches"
        )
        if self.search_url_template and QUERY_PLACEHOLDER not in self.search_url_template:
            raise ValueError(
                "search_url_template must contain {QUERY_PLACEHOLDER}"
            )
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise ValueError("timezone must be a valid IANA time zone") from error


@dataclass(frozen=True)
class ConditionPolicy:
    """Condition rules scoped to one interest, and therefore to one intended use."""

    reject: frozenset[str] = frozenset()
    penalties: dict[str, int] = field(default_factory=dict)
    allow_unknown: bool = True


@dataclass(frozen=True)
class InterestRule:
    """One reason a listing would be useful, with the conditions that use allows."""

    name: str
    # A stable machine-facing identity lets the display name improve without
    # severing outcomes already recorded against the rule. Existing configs
    # need no migration: an omitted id settles to the original name.
    interest_id: str = ""
    purpose: str = "use"
    any_terms: tuple[str, ...] = ()
    all_terms: tuple[str, ...] = ()
    exclude_terms: tuple[str, ...] = ()
    # Words that name an accessory, checked only where they sit beside one of
    # the terms above. Inherited from [interest_defaults]; see InterestDefaults.
    accessory_nouns: tuple[str, ...] = ()
    max_total_cost: Decimal | None = None
    # The floor that separates a thing from its accessories. A guitar cable
    # says "guitar" as loudly as a guitar does, and only the value tells them
    # apart. Paired with max_total_cost: what it is worth, what it may cost.
    minimum_retail: Decimal | None = None
    # None is an ongoing interest. A positive count plus an explicit stable id
    # lets recorded wins retire it without making the matching rule stateful.
    wanted: int | None = None
    minimum_score: int = 0
    # How much this interest matters next to the others. It ranks matches
    # rather than admitting them, so raising it can never smuggle a lot past
    # a threshold; it only decides what gets read first.
    weight: Decimal = Decimal("1")
    condition_profile: str = ""
    condition: ConditionPolicy = field(default_factory=ConditionPolicy)

    def __post_init__(self) -> None:
        if self.wanted is not None:
            if isinstance(self.wanted, bool) or not isinstance(self.wanted, int):
                raise ValueError("wanted must be a whole number")
            require_at_least(self.wanted, 1, field_name="wanted")
            if not self.interest_id.strip():
                raise ValueError("finite interests require an explicit stable id")
        effective_id = self.interest_id.strip() or self.name
        object.__setattr__(self, "interest_id", effective_id)
        require_within(
            self.minimum_score,
            low=LOWEST_SCORE,
            high=HIGHEST_SCORE,
            field_name="minimum_score",
        )
        require_not_negative(self.weight, field_name="weight")
        if self.max_total_cost is not None:
            require_not_negative(self.max_total_cost, field_name="max_total_cost")
        if self.minimum_retail is not None:
            require_not_negative(self.minimum_retail, field_name="minimum_retail")


@dataclass(frozen=True)
class InterestDefaults:
    """Term filters that every interest rule inherits.

    An accessory borrows the name of whatever it attaches to, so a guitar stand
    says "guitar" exactly as loudly as a guitar does. Every rule therefore needs
    the same sentence -- a stand for a guitar is not a guitar, a battery for a
    drill is not a drill -- and before this existed each rule kept its own
    partial copy of it: the monitor rule knew about "monitor stand", the guitar
    rule about "wall mount", and neither knew what the other had learned.

    Saying it once here is what keeps each rule below about what the operator
    wants rather than about what keeps turning up next to it.
    """

    exclude_terms: tuple[str, ...] = ()
    accessory_nouns: tuple[str, ...] = ()

    def applied_to(self, rule: InterestRule) -> InterestRule:
        """The rule, plus every shared filter it does not contradict.

        A rule that explicitly asks for one of these words means it: an interest
        in "guitar stand" must not be silently emptied by a shared "stand". What
        a rule asked for outranks what it inherits, so a shared word that
        appears in the rule's own terms is dropped for that rule alone.
        """
        asked_for = " ".join(rule.any_terms + rule.all_terms)
        inherited = tuple(
            term
            for term in self.exclude_terms
            if term not in asked_for and term not in rule.exclude_terms
        )
        return replace(
            rule,
            exclude_terms=rule.exclude_terms + inherited,
            accessory_nouns=tuple(
                noun for noun in self.accessory_nouns if noun not in asked_for
            ),
        )


@dataclass(frozen=True)
class ValuationSourceConfig:
    """One configured source instance consumed by a reusable adapter."""

    source_id: str
    adapter: str
    enabled: bool = True
    label: str = ""
    categories: tuple[str, ...] = ()
    weight: Decimal = Decimal("1")
    settings: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not _is_safe_identifier(self.source_id):
            raise ValueError(
                f"id {self.source_id!r} may contain only {SOURCE_ID_CHARACTERS}, "
                "and must start with a letter or digit"
            )
        require_finite(self.weight, field_name="weight")
        if self.weight <= 0:
            raise ValueError("weight must be greater than zero")


@dataclass(frozen=True)
class ValuationConfig:
    """Every price source, and the one currency their answers are read in."""

    enabled: bool = False
    currency: str = "USD"
    sources: tuple[ValuationSourceConfig, ...] = ()

    def __post_init__(self) -> None:
        identifiers = [source.source_id for source in self.sources]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("source ids must be unique")


@dataclass(frozen=True)
class LogisticsConfig:
    """Coarse thresholds that decide when handling becomes a question."""

    large_item_policy: LargeItemPolicy = LargeItemPolicy.ASK
    manual_handling_limit_lb: Decimal = Decimal("75")
    large_dimension_threshold_in: Decimal = Decimal("60")
    oversized_terms: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _settle(self, "large_item_policy", LargeItemPolicy)
        require_not_negative(
            self.manual_handling_limit_lb, field_name="manual_handling_limit_lb"
        )
        require_not_negative(
            self.large_dimension_threshold_in, field_name="large_dimension_threshold_in"
        )


@dataclass(frozen=True)
class ScoringConfig:
    """The bar a listing has to clear, and what counts against it."""

    anomaly_minimum_retail: Decimal = Decimal("100")
    anomaly_maximum_ratio: Decimal = Decimal("0.20")
    # A steep discount on something nobody asked for is worth less than a fair
    # price on something wanted, so the catch-all ranks below stated interests.
    anomaly_weight: Decimal = Decimal("0.4")
    minimum_report_score: int = 70
    ending_soon_minutes: int = 20
    condition_penalties: dict[str, int] = field(default_factory=dict)
    rejected_conditions: frozenset[str] = frozenset()
    anomaly_condition: ConditionPolicy = field(default_factory=ConditionPolicy)

    def __post_init__(self) -> None:
        require_within(
            self.minimum_report_score,
            low=LOWEST_SCORE,
            high=HIGHEST_SCORE,
            field_name="minimum_report_score",
        )
        require_not_negative(
            self.anomaly_minimum_retail, field_name="anomaly_minimum_retail"
        )
        _require_rate(self.anomaly_maximum_ratio, field_name="anomaly_maximum_ratio")
        require_not_negative(self.anomaly_weight, field_name="anomaly_weight")
        require_not_negative(self.ending_soon_minutes, field_name="ending_soon_minutes")


@dataclass(frozen=True)
class EmailConfig:
    """Where a report is sent, and which variables hold the secrets."""

    enabled: bool = False
    host_env: str = "AUCTION_LENS_SMTP_HOST"
    port: int = 465
    security: EmailSecurity = EmailSecurity.SSL
    username_env: str = "AUCTION_LENS_SMTP_USERNAME"
    password_env: str = "AUCTION_LENS_SMTP_PASSWORD"
    sender_env: str = "AUCTION_LENS_EMAIL_FROM"
    recipient_env: str = "AUCTION_LENS_EMAIL_TO"
    subject: str = "Auction Lens report"

    def __post_init__(self) -> None:
        _settle(self, "security", EmailSecurity)
        require_within(self.port, low=1, high=HIGHEST_PORT, field_name="port")


@dataclass(frozen=True)
class WebhookConfig:
    """Where a report is posted for somebody waiting on it, and what it may say.

    The address itself is a secret -- anyone holding it can post into the
    channel -- so only the name of the variable holding it lives here, exactly
    as the email credentials do.
    """

    enabled: bool = False
    url_env: str = "AUCTION_LENS_WEBHOOK_URL"
    max_items: int = 10
    username: str = "Auction Lens"

    def __post_init__(self) -> None:
        require_at_least(self.max_items, 1, field_name="max_items")


# Enough of one interest to see what today's crop of it looks like, few enough
# that a busy want leaves room for the others. The same number is what makes a
# search phrase worth printing: see ``reporting.searches``.
DEFAULT_MOST_PER_INTEREST = 3


@dataclass(frozen=True)
class ReportsConfig:
    """How much of the ranking is actually worth putting in front of a person.

    A run can match hundreds of lots and still be correct: the bars decide what
    is worth reporting, and on a good day plenty is. But a report nobody reaches
    the end of has failed at the only thing it does, so the tail is cut.

    Absent means all of them, which is the honest default for a tool that has
    not been told how long its reader's attention is.
    """

    max_items: int | None = None
    # Reading order only. Which lots are worth reporting is settled by the
    # bars above; this decides nothing except what a person sees first.
    order: ReadingOrder = ReadingOrder.PRIORITY
    # How many lots one interest may contribute before the rest are summarised.
    # Ten near-identical keyboards are ten answers to the same question, and a
    # single overall cap lets whichever want happened to be busy today spend
    # the whole report. Unlike max_items there is no "all of them": a report
    # with no per-interest cap is the crowded one this setting exists to fix.
    most_per_interest: int = DEFAULT_MOST_PER_INTEREST

    def __post_init__(self) -> None:
        _settle(self, "order", ReadingOrder)
        if self.max_items is not None:
            require_at_least(self.max_items, 1, field_name="max_items")
        require_at_least(self.most_per_interest, 1, field_name="most_per_interest")


# Anchored to the ceiling a want can actually reach rather than written as a
# bare number, because that is the only thing it means anything against. Two
# points below it: a far branch is worth the drive for a want that is also
# about to close and carries almost no condition penalty. A plain want does
# not clear it, and a value above the ceiling would let nothing wanted through
# at all -- only price-alone bargains, which reach the full 100.
# "auction-lens profile" says which of those a given value means.
FAR_BRANCH_PENALTY_ALLOWANCE = 2
DEFAULT_FAR_MINIMUM_SCORE = HIGHEST_INTEREST_SCORE - FAR_BRANCH_PENALTY_ALLOWANCE


@dataclass(frozen=True)
class LocationPolicy:
    """Which pickup locations are worth collecting from, and which must earn it.

    Distance is not a property of a lot, so it cannot be scored. It is a fact
    about the person: one branch is on the way home and another is half an hour
    in the wrong direction. So a far branch is not forbidden, it is held to a
    higher bar -- only a lot good enough to justify the drive gets through.
    """

    allowed: tuple[str, ...] = ()
    far: tuple[str, ...] = ()
    far_minimum_score: int = DEFAULT_FAR_MINIMUM_SCORE

    def __post_init__(self) -> None:
        require_within(
            self.far_minimum_score,
            low=LOWEST_SCORE,
            high=HIGHEST_SCORE,
            field_name="far_minimum_score",
        )

    def permits(self, location: str) -> bool:
        """An empty allow-list means every pickup location is acceptable."""
        return not self.allowed or _mentions(location, self.allowed)

    def is_far(self, location: str) -> bool:
        return _mentions(location, self.far)

    def already_visiting(self, branches: tuple[str, ...]) -> LocationPolicy:
        """The same map, with today's errands no longer counted as a detour.

        Whether a branch is far is a fact about the week rather than the road:
        the drive is only a cost when it would not otherwise happen. A lot at a
        branch someone is already going to is held to the ordinary bar, so this
        answers "I have to be over there anyway" without lowering the bar for
        the weeks when they do not.
        """
        visiting = tuple(branch.strip().lower() for branch in branches if branch.strip())
        if not visiting:
            return self
        staying_far = tuple(
            name for name in self.far if not any(name in branch for branch in visiting)
        )
        return replace(self, far=staying_far)

    def worth_collecting(self, location: str, score: int) -> bool:
        """Whether this lot, at this score, justifies going to this branch."""
        if not self.is_far(location):
            return True
        return score >= self.far_minimum_score


def _mentions(location: str, names: tuple[str, ...]) -> bool:
    """Match on a name appearing in the branch, so "mesa" finds "Mesa, AZ"."""
    written = location.lower()
    return any(name in written for name in names)


@dataclass(frozen=True)
class AppConfig:
    """Everything one configuration file declares, ready for the pipeline."""

    provider: ProviderConfig
    economics: EconomicsConfig
    acquisition: AcquisitionConfig
    scoring: ScoringConfig
    interests: tuple[InterestRule, ...]
    valuation: ValuationConfig
    logistics: LogisticsConfig
    email: EmailConfig
    webhook: WebhookConfig = field(default_factory=WebhookConfig)
    locations: LocationPolicy = field(default_factory=LocationPolicy)
    reports: ReportsConfig = field(default_factory=ReportsConfig)


def _settle(record: Any, field_name: str, options: type[Choice]) -> None:
    """Replace a settings word with the member it names, on a frozen record.

    TOML hands us "ssl"; a test or a future caller may hand us the member. Both
    arrive here and leave as the member, so no consumer downstream has to wonder
    which it got. Frozen records are written through ``object.__setattr__``
    because ``__post_init__`` runs after the field has already been assigned.
    """
    written = getattr(record, field_name)
    try:
        settled = options(str(written).strip().lower())
    except ValueError as error:
        allowed = ", ".join(option.value for option in options)
        raise ValueError(f"{field_name} must be one of: {allowed}") from error
    object.__setattr__(record, field_name, settled)


def _require_rate(value: Decimal, *, field_name: str) -> None:
    """A rate is a proportion: 0.15 means 15 percent, and 15 is a misplaced percentage."""
    require_not_negative(value, field_name=field_name)
    require_at_most(value, HIGHEST_RATE, field_name=field_name)


def _is_safe_identifier(value: str) -> bool:
    """Accept a name that is safe in a file path and readable in a report."""
    if not value or not value[0].isalnum() or not value.isascii():
        return False
    return all(character.isalnum() or character in "_.-" for character in value)
