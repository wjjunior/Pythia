"""Point-in-time evidence store (§4.5): write embedded chunks, retrieve as-of a timestamp.

`retrieve(market_id, asof, k)` never returns evidence observed after `asof` — the guarantee is
the `observed_at <= asof` predicate in SQL, enforced by the HNSW-indexed cosine search. Candidates
are pulled by vector similarity to the market question, then re-ranked by
similarity x source reliability x freshness before the top-k is returned.
"""

import uuid
from datetime import datetime

from sqlmodel import Session, col, select

from pythia.common import EmbeddingPort, Evidence
from pythia.db import models
from pythia.rag.chunking import chunk
from pythia.rag.ranking import freshness, rank_score

_DEFAULT_HALFLIFE_SECONDS = 7 * 86400.0
_CANDIDATE_MULTIPLIER = 4


def _deterministic_id(*parts: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, "|".join(parts))


def _from_db_evidence(row: models.Evidence) -> Evidence:
    return Evidence(
        id=str(row.id),
        market_id=str(row.market_id),
        source_id=str(row.source_id),
        observed_at=row.observed_at,
        url=row.url,
        content=row.content,
    )


class EvidenceStore:
    def __init__(
        self,
        session: Session,
        embedder: EmbeddingPort,
        *,
        halflife_seconds: float = _DEFAULT_HALFLIFE_SECONDS,
    ) -> None:
        self._session = session
        self._embedder = embedder
        self._halflife_seconds = halflife_seconds

    def add(
        self,
        *,
        market_id: str,
        source_id: str,
        observed_at: datetime,
        content: str,
        url: str | None = None,
    ) -> list[Evidence]:
        chunks = chunk(content)
        vectors = self._embedder.embed(chunks)
        rows: list[models.Evidence] = []
        for index, (text, vector) in enumerate(zip(chunks, vectors, strict=True)):
            row = models.Evidence(
                id=_deterministic_id(
                    "evidence", market_id, source_id, observed_at.isoformat(), str(index)
                ),
                market_id=uuid.UUID(market_id),
                source_id=uuid.UUID(source_id),
                observed_at=observed_at,
                url=url,
                content=text,
                embedding=vector,
            )
            self._session.add(row)
            rows.append(row)
        self._session.commit()
        for row in rows:
            self._session.refresh(row)
        return [_from_db_evidence(row) for row in rows]

    def retrieve(self, market_id: str, asof: datetime, k: int) -> list[Evidence]:
        market = self._session.get(models.Market, uuid.UUID(market_id))
        if market is None:
            return []
        query_vector = self._embedder.embed([market.question])[0]
        # pgvector ships no type stubs, so the cosine_distance comparator is invisible to mypy.
        distance = col(models.Evidence.embedding).cosine_distance(query_vector)  # type: ignore[attr-defined]
        rows = self._session.exec(
            select(models.Evidence, models.Source.reliability, distance.label("distance"))
            .join(models.Source, col(models.Evidence.source_id) == col(models.Source.id))
            .where(col(models.Evidence.market_id) == uuid.UUID(market_id))
            .where(col(models.Evidence.observed_at) <= asof)
            .order_by(distance)
            .limit(max(k * _CANDIDATE_MULTIPLIER, k))
        ).all()
        scored: list[tuple[float, models.Evidence]] = []
        for row, reliability, distance_value in rows:
            similarity = 1.0 - float(distance_value)
            age_seconds = (asof - row.observed_at).total_seconds()
            score = rank_score(
                similarity, reliability, freshness(age_seconds, self._halflife_seconds)
            )
            scored.append((score, row))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [_from_db_evidence(row) for _, row in scored[:k]]
