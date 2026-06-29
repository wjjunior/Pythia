"""Application configuration (Appendix A.3), loaded once from the environment."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from pythia.common.types import Cohort, RecalibrationMethod

Mode = Literal["paper", "live"]
RoutingStrategy = Literal["static", "escalate", "ensemble"]
AggregationMethod = Literal["median", "weighted"]
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
    embedding_api_key: SecretStr | None = None
    embedding_base_url: str | None = None
    openrouter_api_key: SecretStr | None = None
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    model_routing: str | None = None
    model_routing_strategy: RoutingStrategy = "static"
    max_price_per_call: float | None = None
    # Markets estimated per pipeline tick, rotated through the open set by a persistent cursor
    # (PYT-165). At the measured ~$0.005/market this is ~$0.25/tick — well under COST_CAP_PER_RUN
    # ($1) — and the daily kill switch remains the ceiling; covers ~1540 open markets in ~31 ticks
    # vs ~192 at the old 8. None = estimate every open market (the budget guards still bound spend).
    discovery_shortlist_size: int | None = 50

    # Venues
    polymarket_api_base: str = "https://gamma-api.polymarket.com"
    polymarket_data_api_base: str = "https://data-api.polymarket.com"  # positions (§5, PYT-157)
    kalshi_api_base: str | None = None
    metaculus_api_base: str | None = None
    metaculus_api_key: SecretStr | None = None

    # Falcon (smart-money analytics, v2 §3.1/§4.5). Offline via fixtures when token unset.
    falcon_api_base: str | None = None
    falcon_api_token: SecretStr | None = None
    falcon_agent_ids: str = ""  # "dataset:id,..." overrides merged over the confirmed defaults
    candidate_daily_cap: int = 20

    # Trader selection & roster lifecycle (v2 §4.2/§4.3, Appendix)
    tracked_traders_max: int = Field(default=10, ge=0)  # TOTAL non-retired roster cap
    active_traders_max: int = Field(default=5, ge=0)  # active deploy slots, <= tracked
    trader_trial_min_resolved: int = 20
    trader_extreme_price_share_cap: float = Field(default=0.5, ge=0.0, le=1.0)  # scalper guard
    trader_promote_score: float = 0.5
    trader_promote_agree: float = 0.01  # must *beat* the market, not merely tie it (>= gate)
    trader_demote_score: float = 0.0
    trader_demote_agree: float = -1.0
    trader_drawdown_floor: float = 1.0
    trader_bench_ttl_seconds: int = Field(default=1_209_600, ge=0)  # benched-too-long ⇒ retire
    roster_review_interval_seconds: int = 86_400
    market_class_allow: str = "politics,sports,macro,world affairs"
    smart_money_evidence_enabled: bool = False
    backfill_on_track: bool = True
    backfill_on_promote: bool = False
    backfill_max_positions: int = 20
    cross_venue_sources: str = ""
    evidence_sources: str = ""
    newsapi_key: SecretStr | None = None
    crawl_interval_seconds: int = 300
    crawl_close_within_days: int | None = None
    crawl_volume_min: float | None = 1000.0
    crawl_max_markets: int = 500

    # Paper bankroll, sizing & risk caps
    bankroll_paper: float = 10_000.0
    kelly_mult: float = 0.25
    cap_per_trade: float = 0.02
    cap_per_market: float = 0.05
    cap_per_category: float = 0.10
    cap_per_event: float = 0.10  # total exposure across one multi-option event's legs (PYT-140)
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
    # Ensemble aggregation is median-only (PYT-125): calibration-weighting is deferred until
    # per-model track records exist, so no operator-tunable aggregation method is exposed.
    ensemble_size: int = 1
    model_max_attempts: int = 2
    recalibration_method: RecalibrationMethod = "platt"
    recalibration_min_n: int = 50
    recalibration_interval_seconds: int = 86_400  # slow cadence: maps move only as resolutions land
    dispute_risk_lookback: int = 90

    # Calibration gate (§8.5) — a category clears only on measured calibration; invalid-rate cap
    # reuses max_category_invalid_rate. Stays paper-gated until a category clears (prerequisite to
    # real-money execution, which remains unimplemented).
    gate_min_n: int = 50
    gate_brier_margin: float = 0.01
    gate_ev_tolerance: float = 0.05

    # Eval cohorts (§4.11, AC-17) — config-defined market slices for faster, honest calibration.
    eval_cohort_max_horizon_days: float = 7.0
    eval_cohort_price_min: float = 0.10
    eval_cohort_price_max: float = 0.85
    cohort_trade_filter_enabled: bool = False
    cohort_trade_filter: Cohort = Cohort.FAST_7D

    # Post-mortem & improvement loop (§5.5)
    postmortem_surprise_threshold: float = 0.6
    postmortem_min_confidence: float = 0.6
    postmortem_drift_window: int = 20
    postmortem_stale_evidence_days: int = 30
    improvement_validation_min_n: int = 30

    # Observability (§3.10, §8.4) — JSON logs everywhere; Logfire sends only when a token is set
    log_level: LogLevel = "INFO"
    log_json: bool = True
    logfire_token: SecretStr | None = None
    environment: str = "dev"

    @model_validator(mode="after")
    def _active_slots_within_roster(self) -> "Settings":
        if self.active_traders_max > self.tracked_traders_max:
            raise ValueError(
                f"active_traders_max ({self.active_traders_max}) exceeds tracked_traders_max "
                f"({self.tracked_traders_max}); active slots cannot outnumber the tracked roster"
            )
        return self

    @model_validator(mode="after")
    def _cohort_price_band_ordered(self) -> "Settings":
        # A swapped band would silently empty the midband: the shortlist bias and the MIDBAND eval
        # cohort share these bounds, so misordering breaks selection/measurement alignment.
        if self.eval_cohort_price_min > self.eval_cohort_price_max:
            raise ValueError(
                f"eval_cohort_price_min ({self.eval_cohort_price_min}) exceeds "
                f"eval_cohort_price_max ({self.eval_cohort_price_max}); the midband would be empty"
            )
        return self

    @property
    def cross_venue_source_list(self) -> list[str]:
        return [s.strip() for s in self.cross_venue_sources.split(",") if s.strip()]

    @property
    def evidence_source_list(self) -> list[str]:
        return [s.strip() for s in self.evidence_sources.split(",") if s.strip()]

    @property
    def falcon_agent_id_overrides(self) -> dict[str, int]:
        overrides: dict[str, int] = {}
        for entry in self.falcon_agent_ids.split(","):
            item = entry.strip()
            if not item:
                continue
            name, sep, raw = item.partition(":")
            if not sep:
                raise ValueError(f"invalid FALCON_AGENT_IDS entry {item!r}; expected 'dataset:id'")
            overrides[name.strip()] = int(raw.strip())
        return overrides

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
