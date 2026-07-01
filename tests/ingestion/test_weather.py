"""Unit tests (PYT-178): Open-Meteo weather evidence — parsing, serialization, transport."""

from datetime import UTC, date, datetime
from typing import Any

import pytest

from pythia.ingestion.evidence import weather
from pythia.ingestion.evidence.weather import (
    CompositeExtractor,
    LLMWeatherExtractor,
    RegexWeatherExtractor,
    WeatherQuery,
    WeatherTransport,
)

_CLOCK = lambda: datetime(2026, 6, 25, tzinfo=UTC)  # noqa: E731 - fixed clock for year inference


@pytest.mark.parametrize(
    ("question", "city", "target", "fahrenheit"),
    [
        (
            "Will the highest temperature in Warsaw be 36°C on July 1?",
            "Warsaw",
            date(2026, 7, 1),
            False,
        ),
        (
            "Will the lowest temperature in Seoul be 27°C or higher on July 1?",
            "Seoul",
            date(2026, 7, 1),
            False,
        ),
        (
            "Will the highest temperature in San Francisco be between 74-75°F on July 1?",
            "San Francisco",
            date(2026, 7, 1),
            True,
        ),
        (
            "Will the highest temperature in Atlanta be 104°F or higher on July 1?",
            "Atlanta",
            date(2026, 7, 1),
            True,
        ),
    ],
)
def test_regex_extracts_city_date_and_unit(
    question: str, city: str, target: date, fahrenheit: bool
) -> None:
    result = RegexWeatherExtractor(clock=_CLOCK).extract(question)
    assert result == WeatherQuery(city=city, target=target, fahrenheit=fahrenheit)


def test_regex_returns_none_for_a_non_weather_question() -> None:
    assert RegexWeatherExtractor(clock=_CLOCK).extract("Will team A beat team B on July 1?") is None


def test_to_article_serializes_the_daily_series() -> None:
    raw = {
        "city": "Warsaw",
        "fahrenheit": False,
        "daily": {
            "time": ["2026-06-30", "2026-07-01"],
            "temperature_2m_max": [30.0, 33.0],
            "temperature_2m_min": [18.0, 20.0],
        },
    }
    article = weather.to_article(raw)
    assert article is not None
    assert "Warsaw" in article.content
    assert "2026-07-01: high 33.0°C, low 20.0°C" in article.content


def test_to_article_is_none_without_daily_data() -> None:
    assert weather.to_article({"city": "X", "daily": {}}) is None


class _FixedExtractor:
    def __init__(self, query: WeatherQuery | None) -> None:
        self._query = query

    def extract(self, question: str) -> WeatherQuery | None:
        return self._query


def test_transport_geocodes_then_fetches_the_forecast(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def _fake_fetch(url: str, **kwargs: Any) -> dict[str, Any]:
        calls.append(url)
        if "geocoding" in url:
            return {"results": [{"latitude": 52.2, "longitude": 21.0}]}
        return {
            "daily": {
                "time": ["2026-07-01"],
                "temperature_2m_max": [33.0],
                "temperature_2m_min": [20.0],
            }
        }

    monkeypatch.setattr(weather, "fetch_json_evidence", _fake_fetch)
    transport = WeatherTransport(_FixedExtractor(WeatherQuery("Warsaw", date(2026, 7, 1), False)))

    raw = transport.fetch("...")

    assert len(raw) == 1 and raw[0]["city"] == "Warsaw"
    assert any("geocoding" in c for c in calls) and any("forecast" in c for c in calls)
    assert transport.to_article(raw[0]) is not None


def test_transport_returns_nothing_when_the_question_is_unparseable() -> None:
    transport = WeatherTransport(_FixedExtractor(None))
    assert transport.fetch("not a weather question") == []


def test_composite_falls_back_to_the_next_extractor() -> None:
    hit = WeatherQuery("Paris", date(2026, 7, 1), False)
    composite = CompositeExtractor([_FixedExtractor(None), _FixedExtractor(hit)])
    assert composite.extract("q") == hit


class _FakeChat:
    def __init__(self, content: str) -> None:
        self._content = content

    def complete(self, **_: Any) -> dict[str, Any]:
        return {"choices": [{"message": {"content": self._content}}]}


def test_llm_extractor_strips_json_fences() -> None:
    fenced = '```json\n{"city": "Paris", "date": "2026-07-01", "fahrenheit": false}\n```'
    extractor = LLMWeatherExtractor(_FakeChat(fenced), "m", clock=_CLOCK)
    assert extractor.extract("q") == WeatherQuery("Paris", date(2026, 7, 1), False)


def test_to_article_drops_a_payload_with_no_city() -> None:
    raw = {
        "daily": {
            "time": ["2026-07-01"],
            "temperature_2m_max": [33.0],
            "temperature_2m_min": [20.0],
        }
    }
    assert weather.to_article(raw) is None
