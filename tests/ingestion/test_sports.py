"""Unit tests (PYT-179): TheSportsDB sports evidence — parsing, serialization, transport."""

from typing import Any

import pytest

from pythia.ingestion.evidence import sports
from pythia.ingestion.evidence.sports import (
    CompositeExtractor,
    LLMSportsExtractor,
    RegexSportsExtractor,
    SportsQuery,
    SportsTransport,
)


@pytest.mark.parametrize(
    ("question", "team_a", "team_b"),
    [
        ("England vs. DR Congo: O/U 1.5", "England", "DR Congo"),
        ("Detroit Tigers vs. New York Yankees: O/U 10.5", "Detroit Tigers", "New York Yankees"),
        ("Wimbledon ATP: Rafael Jodar vs Pablo Carreno", "Rafael Jodar", "Pablo Carreno"),
        ("Milan: Daniel Rincon vs Mathys Erhard", "Daniel Rincon", "Mathys Erhard"),
    ],
)
def test_regex_extracts_both_teams(question: str, team_a: str, team_b: str) -> None:
    assert RegexSportsExtractor().extract(question) == SportsQuery(team_a=team_a, team_b=team_b)


def test_regex_parses_single_team_formats() -> None:
    extractor = RegexSportsExtractor()
    assert extractor.extract("Will Mjallby AIF win on 2026-07-03?") == SportsQuery("Mjallby AIF")
    assert extractor.extract("Spread: Boston Red Sox (-1.5)") == SportsQuery("Boston Red Sox")


def test_regex_returns_none_when_no_team_is_identifiable() -> None:
    assert RegexSportsExtractor().extract("Who lifts the trophy in 2026?") is None


def test_to_article_serializes_both_teams_recent_form() -> None:
    raw = {
        "forms": [
            {
                "team": "England",
                "events": [
                    {
                        "dateEvent": "2026-06-20",
                        "strHomeTeam": "England",
                        "strAwayTeam": "Spain",
                        "intHomeScore": "2",
                        "intAwayScore": "1",
                    }
                ],
            },
            {"team": "DR Congo", "events": []},
        ]
    }
    article = sports.to_article(raw)
    assert article is not None
    assert "England recent results:" in article.content
    assert "2026-06-20: England 2-1 Spain" in article.content
    assert "DR Congo" not in article.content  # a team with no events contributes no block


def test_to_article_is_none_without_any_form() -> None:
    assert sports.to_article({"forms": []}) is None


class _FixedExtractor:
    def __init__(self, query: SportsQuery | None) -> None:
        self._query = query

    def extract(self, question: str) -> SportsQuery | None:
        return self._query


def test_transport_resolves_each_team_and_fetches_recent_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fake_fetch(url: str, **kwargs: Any) -> dict[str, Any]:
        if "searchteams" in url:
            return {"teams": [{"idTeam": "133604"}]}
        return {"results": [{"strHomeTeam": "A", "strAwayTeam": "B", "dateEvent": "2026-06-20"}]}

    monkeypatch.setattr(sports, "fetch_json_evidence", _fake_fetch)
    transport = SportsTransport(_FixedExtractor(SportsQuery("A", "B")))

    raw = transport.fetch("A vs B")

    assert len(raw) == 1 and len(raw[0]["forms"]) == 2  # both teams resolved
    assert transport.to_article(raw[0]) is not None


def test_transport_drops_a_team_the_search_cannot_resolve(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sports, "fetch_json_evidence", lambda url, **k: {"teams": []})
    transport = SportsTransport(_FixedExtractor(SportsQuery("Nowhere United", "Also Nowhere")))
    assert transport.fetch("...") == []  # no team resolved → no evidence (best-effort)


def test_transport_returns_nothing_when_unparseable() -> None:
    assert SportsTransport(_FixedExtractor(None)).fetch("not head to head") == []


def test_transport_fetches_a_single_team_form(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_fetch(url: str, **kwargs: Any) -> dict[str, Any]:
        if "searchteams" in url:
            return {"teams": [{"idTeam": "1"}]}
        return {"results": [{"strHomeTeam": "A", "strAwayTeam": "B", "dateEvent": "2026-06-20"}]}

    monkeypatch.setattr(sports, "fetch_json_evidence", _fake_fetch)
    transport = SportsTransport(_FixedExtractor(SportsQuery("Mjallby AIF")))

    raw = transport.fetch("Will Mjallby AIF win?")

    assert len(raw) == 1 and len(raw[0]["forms"]) == 1  # single team → one recent-form block


class _FakeChat:
    def __init__(self, content: str) -> None:
        self._content = content

    def complete(self, **_: Any) -> dict[str, Any]:
        return {"choices": [{"message": {"content": self._content}}]}


def test_llm_extractor_parses_and_strips_fences() -> None:
    fenced = '```json\n{"team_a": "Colombia", "team_b": "Ghana"}\n```'
    assert LLMSportsExtractor(_FakeChat(fenced), "m").extract("q") == SportsQuery(
        "Colombia", "Ghana"
    )


def test_composite_prefers_the_regex_then_falls_back() -> None:
    # A question the regex can't split (no " vs ") falls through to the LLM extractor.
    llm = _FixedExtractor(SportsQuery("X", "Y"))
    composite = CompositeExtractor([RegexSportsExtractor(), llm])
    assert composite.extract("Who wins the championship final?") == SportsQuery("X", "Y")
