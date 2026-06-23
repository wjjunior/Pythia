"""Category derivation from the Gamma payload (PYT-71): explicit -> tag -> series -> fallback."""

import json
from pathlib import Path
from typing import Any

from pythia.ingestion.market_crawler import derive_category, to_market

FIXTURE = (
    Path(__file__).resolve().parents[1] / "fixtures" / "polymarket" / "gamma_markets_tagged.json"
)
_TAGGED: list[dict[str, Any]] = json.loads(FIXTURE.read_text())


def _by_question(fragment: str) -> dict[str, Any]:
    return next(m for m in _TAGGED if fragment in m["question"])


def test_uses_the_first_tag_label_as_the_category() -> None:
    assert derive_category(_by_question("Harvey Weinstein")) == "Movies"
    assert derive_category(_by_question("MegaETH")) == "Crypto"
    assert derive_category(_by_question("Spain win")) == "Sports"


def test_keeps_the_venue_label_verbatim() -> None:
    assert derive_category(_by_question("Xi Jinping")) == "world affairs"


def test_skips_an_empty_first_tag_label() -> None:
    assert derive_category({"tags": [{"label": "  "}, {"label": "Politics"}]}) == "Politics"


def test_prefers_an_explicit_category_field_when_present() -> None:
    assert derive_category({"category": "crypto", "tags": [{"label": "Politics"}]}) == "crypto"


def test_falls_back_to_series_title_when_no_tags() -> None:
    raw = {"tags": [], "events": [{"series": [{"title": "FIFA World Cup"}]}]}
    assert derive_category(raw) == "FIFA World Cup"


def test_falls_back_to_uncategorized_when_no_signal() -> None:
    assert derive_category({}) == "uncategorized"
    assert derive_category({"tags": [], "events": [{"series": []}]}) == "uncategorized"


def test_to_market_assigns_the_derived_category() -> None:
    market = to_market(_by_question("Harvey Weinstein"), "00000000-0000-0000-0000-000000000001")
    assert market.category == "Movies"
