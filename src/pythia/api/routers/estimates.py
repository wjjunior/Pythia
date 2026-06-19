"""Estimate detail (§A.1): estimate + critic notes + cited evidence."""

import uuid

from fastapi import APIRouter, HTTPException

from pythia.api.deps import SessionDep
from pythia.api.schemas import EstimateDetail
from pythia.db.repositories import EstimateRepository, EvidenceRepository

router = APIRouter(tags=["estimates"])


@router.get("/api/estimates/{estimate_id}")
def estimate_detail(estimate_id: uuid.UUID, session: SessionDep) -> EstimateDetail:
    estimate = EstimateRepository(session).get(str(estimate_id))
    if estimate is None:
        raise HTTPException(status_code=404, detail="estimate not found")
    evidence = EvidenceRepository(session).get_by_ids(estimate.evidence_ids)
    return EstimateDetail(estimate=estimate, evidence=evidence)
