"""Metaculus adapter (§4.10): the community prediction is a second, independently-formed baseline.

The recency-weighted community prediction lives at
`question.aggregations.recency_weighted.latest.centers[0]` (per the Metaculus API). That aggregation
is a restricted, approval-gated field: when the token tier does not include it the value is null and
the adapter yields no price (the quote is discarded), rather than inventing one. The live transport
walks posts then question detail because the list endpoint does not serialize aggregations.
"""

from datetime import datetime
from typing import Any

from pythia.common import Venue
from pythia.ingestion.venues.base import VenueCandidate, fetch_json

VENUE = Venue.METACULUS
POSTS_URL = "https://www.metaculus.com/api/posts/"
QUESTIONS_URL = "https://www.metaculus.com/api/questions"
_USER_AGENT = "Mozilla/5.0 (compatible; PythiaResearch/0.1)"


def _community_prediction(raw: dict[str, Any]) -> float | None:
    aggregations = raw.get("aggregations") or {}
    recency = aggregations.get("recency_weighted") or {}
    latest = recency.get("latest")
    centers = latest.get("centers") if isinstance(latest, dict) else None
    if not centers:
        return None
    return float(centers[0])


def to_candidate(raw: dict[str, Any]) -> VenueCandidate | None:
    close = raw.get("scheduled_close_time")
    return VenueCandidate(
        external_ref=str(raw["id"]),
        question=str(raw["title"]),
        close_time=datetime.fromisoformat(close) if close else None,
        yes_price=_community_prediction(raw),
    )


class MetaculusHttpTransport:
    """Live transport: posts list → per-question detail (which carries the aggregation)."""

    def __init__(self, api_key: str, *, use_live: bool = False, limit: int = 20) -> None:
        self._api_key = api_key
        self._use_live = use_live
        self._limit = limit

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Token {self._api_key}",
            "Accept": "application/json",
            "User-Agent": _USER_AGENT,
        }

    def fetch(self) -> list[dict[str, Any]]:
        posts = fetch_json(
            POSTS_URL,
            venue=VENUE.value,
            use_live=self._use_live,
            headers=self._headers(),
            params={
                "statuses": "open",
                "forecast_type": "binary",
                "limit": self._limit,
                "order_by": "-nr_forecasters",
            },
        )
        questions: list[dict[str, Any]] = []
        for post in posts.get("results", []):
            question_id = post["question"]["id"]
            detail = fetch_json(
                f"{QUESTIONS_URL}/{question_id}/",
                venue=VENUE.value,
                use_live=self._use_live,
                headers=self._headers(),
            )
            questions.append(
                {
                    "id": detail["id"],
                    "title": detail.get("title") or post.get("title"),
                    "scheduled_close_time": detail.get("scheduled_close_time"),
                    "aggregations": detail.get("aggregations"),
                }
            )
        return questions
