from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ipp_joblog.ipp import Attribute, decode_response
from ipp_joblog.jobs import Job
from ipp_joblog.store import JobStore

FIXTURES = Path(__file__).parent / "fixtures"
CET = timezone(timedelta(hours=1))


@pytest.fixture
def get_jobs_response() -> bytes:
    """A real Get-Jobs response recorded from an HP Color LaserJet flow MFP M880."""
    return (FIXTURES / "get-jobs-completed.ipp").read_bytes()


@pytest.fixture
def job_groups(get_jobs_response: bytes) -> list[dict]:
    _, groups = decode_response(get_jobs_response)
    return [group for group in groups if "job-id" in group]


@pytest.fixture
def store(tmp_path: Path) -> JobStore:
    with JobStore(tmp_path / "jobs.sqlite3") as opened:
        yield opened


@pytest.fixture
def cet() -> timezone:
    """The printer's local zone in the recorded fixture."""
    return CET


@pytest.fixture
def make_job():
    """Factory for job records; pass keyword overrides for the fields under test."""
    return _make_job


def _make_job(**overrides) -> Job:
    defaults = {
        "key": "2026-09-10T20:45:28+01:00#19",
        "job_id": 19,
        "job_name": "Layout Draft.indd",
        "user_name": "alice",
        "state": "completed",
        "impressions": 72,
        "sheets": 36,
        "color_mode": "monochrome",
        "sides": "two-sided-long-edge",
        "document_format": "application/pdf",
        "created_at": datetime(2026, 9, 10, 20, 45, 28, tzinfo=CET),
        "completed_at": datetime(2026, 9, 10, 20, 48, 27, tzinfo=CET),
    }
    return Job(**{**defaults, **overrides})


@pytest.fixture
def attributes():
    """Build an IPP attribute group from plain Python values."""

    def build(**values) -> dict[str, Attribute]:
        return {
            name.replace("_", "-"): Attribute(0, name.replace("_", "-"), [value])
            for name, value in values.items()
        }

    return build
