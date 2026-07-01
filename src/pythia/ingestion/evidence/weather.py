"""Open-Meteo weather adapter (PYT-178): domain evidence for weather markets.

News is useless for "will the high in Warsaw be 36°C on July 1?"; the forecast + recent temperatures
are exactly the signal the estimator needs. No API key, HTTPS, global (not geo-restricted, §8.6). A
weather market is routed here (not to the news sources) by category. The market question is parsed
into (city, target date, unit) — regex for the templated Polymarket questions, with a cheap-LLM
fallback for unrecognised formats; an unparseable question yields no evidence (safe).
"""

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol

from pythia.common.logging import get_logger
from pythia.ingestion.evidence.base import EvidenceArticle, fetch_json_evidence

_log = get_logger(__name__)

SOURCE_NAME = "open-meteo"
SOURCE_RELIABILITY = 0.9  # authoritative meteorological data, not a news aggregator
CATEGORIES = frozenset({"weather"})  # markets routed to this source instead of the news sources

_GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
_PRIOR_DAYS = 3  # recent-trend window serialized alongside the target-day forecast

# "Will the highest temperature in Warsaw be 36°C on July 1?" and its variants (°F, ranges, or
# higher/below). City sits between "in " and " be "; the unit and the month/day are pulled out too.
_QUESTION_RE = re.compile(
    r"temperature in (?P<city>.+?) be .*?(?P<unit>°C|°F).*? "
    r"on (?P<month>[A-Z][a-z]+) (?P<day>\d{1,2})",
)
_MONTHS = {
    m: i
    for i, m in enumerate(
        [
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ],
        start=1,
    )
}


@dataclass(frozen=True, slots=True)
class WeatherQuery:
    city: str
    target: date
    fahrenheit: bool


class WeatherQueryExtractor(Protocol):
    def extract(self, question: str) -> WeatherQuery | None: ...


def _infer_year(month: int, day: int, now: date) -> int:
    # Polymarket weather questions carry no year; these markets are near-term, so pick the current
    # year unless that date is well in the past (then it's the coming year).
    candidate = date(now.year, month, day)
    return now.year + 1 if candidate < now - timedelta(days=60) else now.year


class RegexWeatherExtractor:
    """Deterministic parse of the templated Polymarket weather question. No LLM, no I/O."""

    def __init__(self, *, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))

    def extract(self, question: str) -> WeatherQuery | None:
        match = _QUESTION_RE.search(question)
        if match is None:
            return None
        month = _MONTHS.get(match.group("month"))
        if month is None:
            return None
        day = int(match.group("day"))
        try:
            year = _infer_year(month, day, self._clock().date())
            target = date(year, month, day)
        except ValueError:
            return None
        return WeatherQuery(
            city=match.group("city").strip(),
            target=target,
            fahrenheit=match.group("unit") == "°F",
        )


class LLMWeatherExtractor:
    """Cheap-LLM fallback for questions the regex misses; returns None on any failure (safe)."""

    def __init__(
        self,
        transport: Any,  # ChatTransport (agents.llm_port); Any avoids an agents import cycle
        model: str,
        *,
        clock: Callable[[], datetime] | None = None,
        max_price: float | None = None,
    ) -> None:
        self._transport = transport
        self._model = model
        self._clock = clock or (lambda: datetime.now(UTC))
        self._max_price = max_price

    def extract(self, question: str) -> WeatherQuery | None:
        prompt = (
            "Extract the city, target date, and unit from this weather market question. "
            'Reply with JSON only: {"city": str, "date": "YYYY-MM-DD", "fahrenheit": bool}. '
            f"Today is {self._clock().date().isoformat()}. Question: {question}"
        )
        try:
            data = self._transport.complete(
                model=self._model,
                messages=[{"role": "user", "content": prompt}],
                max_price=self._max_price,
            )
            content = data["choices"][0]["message"]["content"]
            parsed = json.loads(content)
            return WeatherQuery(
                city=str(parsed["city"]).strip(),
                target=date.fromisoformat(str(parsed["date"])),
                fahrenheit=bool(parsed["fahrenheit"]),
            )
        except (KeyError, IndexError, ValueError, TypeError) as exc:
            _log.warning("weather_llm_extract_failed", error=str(exc))
            return None
        except Exception as exc:  # transport/network failure — degrade, never abort the tick
            _log.warning("weather_llm_extract_failed", error=str(exc))
            return None


class CompositeExtractor:
    """Try each extractor in order; the first hit wins (regex before the paid LLM)."""

    def __init__(self, extractors: list[WeatherQueryExtractor]) -> None:
        self._extractors = extractors

    def extract(self, question: str) -> WeatherQuery | None:
        for extractor in self._extractors:
            result = extractor.extract(question)
            if result is not None:
                return result
        return None


def to_article(raw: dict[str, Any]) -> EvidenceArticle | None:
    daily = raw.get("daily") or {}
    times = daily.get("time") or []
    highs = daily.get("temperature_2m_max") or []
    lows = daily.get("temperature_2m_min") or []
    if not times or len(highs) != len(times) or len(lows) != len(times):
        return None
    unit = "°F" if raw.get("fahrenheit") else "°C"
    lines = [
        f"{t}: high {hi}{unit}, low {lo}{unit}"
        for t, hi, lo in zip(times, highs, lows, strict=True)
    ]
    content = (
        f"Open-Meteo temperatures for {raw.get('city')} (recent days through the target date):\n"
        + "\n".join(lines)
    )
    return EvidenceArticle(content=content, url=None, published_at=None)


class WeatherTransport:
    """Live Open-Meteo transport — geocode the city, fetch daily highs/lows near the target date."""

    name = SOURCE_NAME
    reliability = SOURCE_RELIABILITY
    categories = CATEGORIES

    def __init__(self, extractor: WeatherQueryExtractor, *, use_live: bool = False) -> None:
        self._extractor = extractor
        self._use_live = use_live

    def fetch(self, query: str) -> list[dict[str, Any]]:
        parsed = self._extractor.extract(query)
        if parsed is None:
            return []
        geo = fetch_json_evidence(
            _GEOCODING_URL,
            source=SOURCE_NAME,
            use_live=self._use_live,
            params={"name": parsed.city, "count": 1},
        )
        results = geo.get("results") or []
        if not results:
            return []
        location = results[0]
        payload = fetch_json_evidence(
            _FORECAST_URL,
            source=SOURCE_NAME,
            use_live=self._use_live,
            params={
                "latitude": location["latitude"],
                "longitude": location["longitude"],
                "daily": "temperature_2m_max,temperature_2m_min",
                "start_date": (parsed.target - timedelta(days=_PRIOR_DAYS)).isoformat(),
                "end_date": parsed.target.isoformat(),
                "timezone": "auto",
                "temperature_unit": "fahrenheit" if parsed.fahrenheit else "celsius",
            },
        )
        return [
            {
                "city": parsed.city,
                "fahrenheit": parsed.fahrenheit,
                "daily": payload.get("daily") or {},
            }
        ]

    def to_article(self, raw: dict[str, Any]) -> EvidenceArticle | None:
        return to_article(raw)
