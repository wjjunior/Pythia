"""Application configuration (Appendix A.3), loaded once from the environment."""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

Mode = Literal["paper", "live"]
RoutingStrategy = Literal["static", "escalate", "ensemble"]
AggregationMethod = Literal["median", "weighted"]
RecalibrationMethod = Literal["platt", "beta", "isotonic"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]


class ExecutionModeError(RuntimeError):
    """Raised when an execution path runs outside paper mode."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Core infrastructure
    database_url: str
    redis_url: str | None = None

    # Mode & venue gating (§8.6)
    mode: Mode = "paper"
    use_live_venues: bool = False

    # LLM / embeddings — leave keys unset to run the deterministic stub
    llm_provider: str | None = None
    llm_model: str | None = None
    embedding_provider: str | None = None
    embedding_model: str | None = None
    embedding_api_key: str | None = None
    embedding_base_url: str | None = None
    openrouter_api_key: str | None = None
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    model_routing: str | None = None
    model_routing_strategy: RoutingStrategy = "static"
    max_price_per_call: float | None = None

    # Venues
    polymarket_api_base: str = "https://gamma-api.polymarket.com"
    kalshi_api_base: str | None = None
    metaculus_api_base: str | None = None
    metaculus_api_key: str | None = None
    cross_venue_sources: str = ""
    crawl_interval_seconds: int = 300

    # Paper bankroll, sizing & risk caps
    bankroll_paper: float = 10_000.0
    kelly_mult: float = 0.25
    cap_per_trade: float = 0.02
    cap_per_market: float = 0.05
    cap_per_category: float = 0.10
    cap_daily: float = 0.05
    cap_weekly: float = 0.15
    cap_absolute: float = 1.0
    min_source_reliability: float = 0.5
    min_confidence: float = 0.5
    max_p_resol_severity: float = 0.5
    max_category_invalid_rate: float = 0.15
    divergence_penalty_weight: float = 1.0

    # Cost guards
    cost_cap_per_run: float = 1.0
    cost_budget_daily: float = 10.0

    # Growth simulation
    ruin_floor: float = 0.5
    growth_sim_paths: int = 10_000
    growth_sim_horizon: int = 365
    growth_sim_seed: int = 42

    # Forecast aggregation & recalibration
    aggregation_method: AggregationMethod = "median"
    ensemble_size: int = 1
    recalibration_method: RecalibrationMethod = "platt"
    recalibration_min_n: int = 50
    dispute_risk_lookback: int = 90

    # Post-mortem & improvement loop (§5.5)
    postmortem_surprise_threshold: float = 0.6
    postmortem_min_confidence: float = 0.6
    postmortem_drift_window: int = 20
    postmortem_stale_evidence_days: int = 30
    improvement_min_n: int = 30

    # Observability (§3.10, §8.4) — JSON logs everywhere; Logfire sends only when a token is set
    log_level: LogLevel = "INFO"
    log_json: bool = True
    logfire_token: str | None = None
    environment: str = "dev"

    @property
    def cross_venue_source_list(self) -> list[str]:
        return [s.strip() for s in self.cross_venue_sources.split(",") if s.strip()]

    @property
    def llm_enabled(self) -> bool:
        return bool(self.openrouter_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()


def ensure_paper_mode(settings: Settings) -> None:
    if settings.mode != "paper":
        raise ExecutionModeError(
            f"MODE is {settings.mode!r}; only paper mode is supported. Real-money "
            "execution stays gated until the calibration gate is cleared (§3.2)."
        )
