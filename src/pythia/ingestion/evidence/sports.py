"""TheSportsDB adapter (PYT-179): domain evidence for sports markets.

News is useless for "England vs. DR Congo: O/U 1.5"; each team's recent form is the signal. Free
test key, HTTPS, multi-sport, not geo-restricted (§8.6). A sports market is routed here (not the
news sources) by category. The question is parsed into (team A, team B) — MVP covers the
"{A} vs {B}" head-to-head format; a string split first, with a cheap-LLM fallback. Unparseable, or
a team the search can't resolve, yields no evidence (safe, best-effort).
"""

import json
import re
from dataclasses import dataclass
from typing import Any, Protocol

from pythia.common.logging import get_logger
from pythia.ingestion.evidence.base import EvidenceArticle, fetch_json_evidence

_log = get_logger(__name__)

SOURCE_NAME = "thesportsdb"
SOURCE_RELIABILITY = 0.7  # recent form is informative but not the authoritative outcome
DEFAULT_CATEGORIES = frozenset({"sports", "tennis", "soccer", "esports"})
_RECENT_EVENTS = 5

_VS_RE = re.compile(r"\s+vs\.?\s+", re.IGNORECASE)  # "{A} vs {B}" or "{A} vs. {B}"
# Single-team formats (PYT-180): moneyline "Will {Team} win …" and "Spread: {Team} (-1.5)".
_WIN_RE = re.compile(r"\bWill\s+(?P<team>.+?)\s+win\b", re.IGNORECASE)
_SPREAD_RE = re.compile(r"\bSpread:\s*(?P<team>.+?)\s*\(", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class SportsQuery:
    team_a: str
    team_b: str | None = None  # head-to-head sets both; a single-team market leaves this None


class SportsQueryExtractor(Protocol):
    def extract(self, question: str) -> SportsQuery | None: ...


class RegexSportsExtractor:
    """Parse "{A} vs {B}" or a single-team market ("Will {Team} win", "Spread: {Team}"). No I/O."""

    def extract(self, question: str) -> SportsQuery | None:
        match = _VS_RE.search(question)
        if match is not None:
            team_a = question[: match.start()].rsplit(": ", 1)[-1].strip()
            team_b = question[match.end() :].split(":", 1)[0].strip()
            return SportsQuery(team_a=team_a, team_b=team_b) if team_a and team_b else None
        for pattern in (_WIN_RE, _SPREAD_RE):
            single = pattern.search(question)
            if single is not None:
                team = single.group("team").strip()
                return SportsQuery(team_a=team) if team else None
        return None


class LLMSportsExtractor:
    """Cheap-LLM fallback for questions the split misses; returns None on any failure (safe)."""

    def __init__(self, transport: Any, model: str, *, max_price: float | None = None) -> None:
        self._transport = transport  # ChatTransport (agents.llm_port); Any avoids an import cycle
        self._model = model
        self._max_price = max_price

    def extract(self, question: str) -> SportsQuery | None:
        prompt = (
            "Extract the team(s)/player(s) this sports market is about. "
            'Reply with JSON only: {"team_a": str, "team_b": str}. '
            "For a single-team market (e.g. moneyline or spread), put the team in team_a and leave "
            'team_b "". If no team is identifiable, reply {}. '
            f"Question: {question}"
        )
        try:
            data = self._transport.complete(
                model=self._model,
                messages=[{"role": "user", "content": prompt}],
                max_price=self._max_price,
            )
            parsed = json.loads(_strip_json_fences(data["choices"][0]["message"]["content"]))
            team_a = str(parsed.get("team_a") or "").strip()
            team_b = str(parsed.get("team_b") or "").strip()
            return SportsQuery(team_a=team_a, team_b=team_b or None) if team_a else None
        except (KeyError, IndexError, ValueError, TypeError) as exc:
            _log.warning("sports_llm_extract_failed", error=str(exc))
            return None
        except Exception as exc:  # transport/network failure — degrade, never abort the tick
            _log.warning("sports_llm_extract_failed", error=str(exc))
            return None


def _strip_json_fences(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[-1] if "\n" in stripped else ""
        stripped = stripped.rsplit("```", 1)[0]
    return stripped.strip()


class CompositeExtractor:
    """Try each extractor in order; the first hit wins (the string split before the paid LLM)."""

    def __init__(self, extractors: list[SportsQueryExtractor]) -> None:
        self._extractors = extractors

    def extract(self, question: str) -> SportsQuery | None:
        for extractor in self._extractors:
            result = extractor.extract(question)
            if result is not None:
                return result
        return None


def _format_events(events: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for event in events[:_RECENT_EVENTS]:
        home = event.get("strHomeTeam")
        away = event.get("strAwayTeam")
        if not home or not away:
            continue
        score = f"{event.get('intHomeScore')}-{event.get('intAwayScore')}"
        lines.append(f"{event.get('dateEvent')}: {home} {score} {away}")
    return lines


def to_article(raw: dict[str, Any]) -> EvidenceArticle | None:
    blocks: list[str] = []
    for form in raw.get("forms") or []:
        team = str(form.get("team") or "").strip()
        lines = _format_events(form.get("events") or [])
        if team and lines:
            blocks.append(f"{team} recent results:\n" + "\n".join(lines))
    if not blocks:
        return None
    return EvidenceArticle(
        content="TheSportsDB recent form:\n\n" + "\n\n".join(blocks), url=None, published_at=None
    )


class SportsTransport:
    """Live TheSportsDB transport — resolve each team, fetch its recent results as evidence."""

    name = SOURCE_NAME
    reliability = SOURCE_RELIABILITY

    def __init__(
        self,
        extractor: SportsQueryExtractor,
        *,
        api_key: str = "123",
        use_live: bool = False,
        categories: frozenset[str] = DEFAULT_CATEGORIES,
    ) -> None:
        self._extractor = extractor
        self._base = f"https://www.thesportsdb.com/api/v1/json/{api_key}"
        self._use_live = use_live
        self.categories = categories

    def fetch(self, query: str) -> list[dict[str, Any]]:
        parsed = self._extractor.extract(query)
        if parsed is None:
            return []
        teams = [parsed.team_a] + ([parsed.team_b] if parsed.team_b else [])  # single-team ⇒ one
        forms = [
            {"team": team, "events": events}
            for team in teams
            if (events := self._recent_events(team)) is not None
        ]
        return [{"forms": forms}] if forms else []

    def _recent_events(self, team: str) -> list[dict[str, Any]] | None:
        search = fetch_json_evidence(
            f"{self._base}/searchteams.php",
            source=SOURCE_NAME,
            use_live=self._use_live,
            params={"t": team},
        )
        teams = search.get("teams") or []
        team_id = teams[0].get("idTeam") if teams else None
        if not team_id:
            return None  # best-effort: a team the search can't resolve yields no evidence
        last = fetch_json_evidence(
            f"{self._base}/eventslast.php",
            source=SOURCE_NAME,
            use_live=self._use_live,
            params={"id": team_id},
        )
        events: list[dict[str, Any]] = last.get("results") or last.get("events") or []
        return events

    def to_article(self, raw: dict[str, Any]) -> EvidenceArticle | None:
        return to_article(raw)
