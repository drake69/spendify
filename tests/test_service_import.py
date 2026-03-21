"""Tests for ImportService."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine

from db.models import Base, ImportJob, get_session
from services.import_service import ImportService


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def engine():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture
def svc(engine):
    return ImportService(engine)


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_create_and_get_job(svc):
    svc.create_job(n_files=3)

    latest = svc.get_latest_job()
    assert latest is not None
    assert latest.n_files == 3
    assert latest.status == "running"


def test_get_latest_job_returns_none_when_empty(svc):
    latest = svc.get_latest_job()
    assert latest is None


def test_update_job(engine):
    svc = ImportService(engine)
    svc.create_job(n_files=2)

    first = svc.get_latest_job()
    assert first is not None
    job_id = first.id

    svc.update_job(job_id, status="completed", progress=1.0, n_transactions=10)

    latest = svc.get_latest_job()
    assert latest.status == "completed"
    assert float(latest.progress) == 1.0
    assert latest.n_transactions == 10


def test_update_job_not_found(svc):
    # Should not raise; silently ignores missing job
    svc.update_job(9999, status="completed")


def test_reset_stale_jobs(engine):
    svc = ImportService(engine)
    # Create two "running" jobs manually
    job1 = svc.create_job(n_files=1)
    job2 = svc.create_job(n_files=2)

    n_reset = svc.reset_stale_jobs()
    assert n_reset == 2

    latest = svc.get_latest_job()
    assert latest.status == "error"


def test_reset_stale_jobs_no_running(svc):
    """reset_stale_jobs returns 0 when there are no running jobs."""
    n = svc.reset_stale_jobs()
    assert n == 0


def test_create_multiple_jobs_get_latest(engine):
    svc = ImportService(engine)
    svc.create_job(n_files=1)
    svc.create_job(n_files=5)

    latest = svc.get_latest_job()
    assert latest is not None
    assert latest.n_files == 5
