"""The complete application configuration assembled from feature records."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..values import require_at_least
from .interests import InterestRule, ScoringConfig
from .logistics import LocationPolicy, LogisticsConfig
from .pricing import EconomicsConfig, ValuationConfig
from .provider import AcquisitionConfig, ProviderConfig
from .reports import EmailConfig, ReportsConfig, WebhookConfig


@dataclass(frozen=True)
class JudgingConfig:
    """Where the optional local judge listens and how much work it may do."""

    enabled: bool = False
    endpoint: str = "http://localhost:11434"
    model: str = "qwen2.5:7b-instruct"
    timeout_seconds: int = 60
    workers: int = 4

    def __post_init__(self) -> None:
        require_at_least(self.timeout_seconds, 1, field_name="timeout_seconds")
        require_at_least(self.workers, 1, field_name="workers")


@dataclass(frozen=True)
class AppConfig:
    """Everything one configuration file declares, ready for a daily run."""

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
    judging: JudgingConfig = field(default_factory=JudgingConfig)
