"""§8.6 defense-in-depth on the live evidence egress (F-10/PYT-130): refuse without the flag."""

import pytest

from pythia.ingestion.evidence.newsapi import NewsApiTransport
from pythia.ingestion.market_crawler import LiveVenueAccessError


def test_should_refuse_a_live_newsapi_fetch_when_use_live_is_off() -> None:
    transport = NewsApiTransport("dummy-key", use_live=False)

    with pytest.raises(LiveVenueAccessError, match="USE_LIVE_VENUES is off"):
        transport.fetch("any query")
