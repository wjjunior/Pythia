"""Refresh the cross-venue fixtures from live APIs (§4.10, §8.6) — operator-run, VPN-gated.

This is the ONLY step that touches a live venue. Kalshi is US-regulated and geo-restricted: run it
with the operator's VPN enabled. Metaculus' community prediction needs approved API access
(METACULUS_API_KEY); without it the question metadata is captured but the price is withheld.
CI never runs this — tests replay the recorded fixtures offline.

    uv run python -m scripts.capture_venues          # all venues it can reach
    uv run python -m scripts.capture_venues kalshi   # one venue
"""

import json
import sys
from pathlib import Path

from pythia.config import get_settings
from pythia.ingestion import LiveVenueAccessError
from pythia.ingestion.venues import manifold
from pythia.ingestion.venues.kalshi import KalshiHttpTransport
from pythia.ingestion.venues.manifold import ManifoldHttpTransport
from pythia.ingestion.venues.metaculus import MetaculusHttpTransport

_FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "venues"
_LIMIT = 12


def _capture_manifold() -> None:
    markets = ManifoldHttpTransport(limit=25).fetch()
    binary = [m for m in markets if manifold.to_candidate(m) is not None][:_LIMIT]
    _write("manifold_markets.json", binary)


def _capture_kalshi() -> None:
    print("⚠️  Kalshi is geo-restricted — enable the VPN before continuing (§8.6).")
    markets = KalshiHttpTransport(limit=25).fetch()[:_LIMIT]
    _write("kalshi_markets.json", {"markets": markets})


def _capture_metaculus() -> None:
    key = get_settings().metaculus_api_key
    if not key:
        print("· skipping Metaculus: METACULUS_API_KEY not set (approved API access required).")
        return
    questions = MetaculusHttpTransport(key, limit=_LIMIT).fetch()
    _write(
        "metaculus_questions.json",
        {
            "_capture_note": "Metadata captured live; recency_weighted.latest.centers is null "
            "unless the token tier includes approved aggregation access.",
            "questions": questions,
        },
    )


def _write(name: str, payload: object) -> None:
    path = _FIXTURES / name
    path.write_text(json.dumps(payload, indent=2))
    print(f"✓ wrote {path.relative_to(_FIXTURES.parents[2])}")


_CAPTURERS = {
    "manifold": _capture_manifold,
    "kalshi": _capture_kalshi,
    "metaculus": _capture_metaculus,
}


def main() -> None:
    requested = sys.argv[1:] or list(_CAPTURERS)
    for venue in requested:
        capture = _CAPTURERS.get(venue)
        if capture is None:
            print(f"unknown venue: {venue} (choose from {', '.join(_CAPTURERS)})")
            continue
        try:
            capture()
        except LiveVenueAccessError as exc:
            print(f"✗ {venue}: {exc}")


if __name__ == "__main__":
    main()
