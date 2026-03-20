"""Router: /import"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from api.dependencies import get_import_service
from api.schemas import ImportJobResponse
from services import ImportService

router = APIRouter(prefix="/import", tags=["import"])


def _job_to_schema(job) -> ImportJobResponse:
    return ImportJobResponse(
        id=job.id,
        status=job.status,
        progress=float(job.progress or 0.0),
        status_message=job.status_message,
        detail_message=job.detail_message,
        n_transactions=job.n_transactions or 0,
        n_files=job.n_files or 0,
        started_at=job.started_at,
        completed_at=job.completed_at,
    )


@router.get("/jobs/latest", response_model=ImportJobResponse | None)
def get_latest_job(svc: ImportService = Depends(get_import_service)):
    job = svc.get_latest_job()
    if job is None:
        return None
    return _job_to_schema(job)
