"""Job records read from a printer's IPP job history."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, TypeVar

from ipp_joblog.ipp import Attribute, IppClient

# What a printer must report for the page counts to mean anything.
REQUIRED_ATTRIBUTES = ("job-originating-user-name", "job-impressions-completed")
OPTIONAL_ATTRIBUTES = (
    "job-media-sheets-completed",
    "print-color-mode",
    "sides",
    "date-time-at-completed",
)

# print-color-mode values that cost no colour pages. Anything else is colour.
# The store repeats this set in SQL, so it lives here and is imported there.
MONOCHROME_MODES = ("", "monochrome", "auto-monochrome", "process-monochrome")

# RFC 8011 job-state values. Only 7-9 mean the printer is finished with a job.
JOB_STATES = {
    3: "pending",
    4: "pending-held",
    5: "processing",
    6: "processing-stopped",
    7: "canceled",
    8: "aborted",
    9: "completed",
}

_ZEROED_UUID_SUFFIX = "0" * 12

T = TypeVar("T")


def _value(attributes: dict[str, Attribute], name: str, kind: type[T], default: T) -> T:
    """Read one attribute, falling back when it is absent or the wrong type.

    Every attribute except ``job-id`` is optional on some printer, so a missing
    or unexpected value must degrade rather than raise.
    """
    attribute = attributes.get(name)
    value: Any = attribute.value if attribute is not None else None
    return value if isinstance(value, kind) else default


def _text(attributes: dict[str, Attribute], name: str) -> str:
    return _value(attributes, name, str, "")


def _number(attributes: dict[str, Attribute], name: str) -> int:
    return _value(attributes, name, int, 0)


def _moment(attributes: dict[str, Attribute], name: str) -> datetime | None:
    return _value(attributes, name, datetime, None)


def _optional_number(attributes: dict[str, Attribute], name: str) -> int | None:
    """``None`` when the printer did not report the attribute at all.

    Distinct from zero on purpose: a Brother MFC-L3770CDW never reports
    job-media-sheets-completed, and "did not say" must not read as "used no
    paper".
    """
    return _value(attributes, name, int, None)


def job_key(attributes: dict[str, Attribute]) -> str:
    """A stable identity for one job, best source first.

    ``job-uuid`` (PWG 5100.13) is the right answer where a printer fills it in,
    but HP FutureSmart zeroes it for everything except Mopria jobs. ``job-id``
    alone will not do either, because some printers restart it at 1 after a
    reboot, so it is paired with a clock.

    That clock is ``time-at-creation`` -- printer uptime in seconds -- in
    preference to the wall-clock ``date-time-at-creation``, because the
    wall-clock value is not always stable. A Brother MFC-L3770CDW returns a
    creation time that jitters by a second between two reads of the *same* job
    while its uptime counter stays put; keying on the wall clock there yields a
    fresh key per poll and counts the job again every time.

    The cost is that uptime restarts at zero on reboot, so a collision needs a
    new job to land on both the same uptime second and the same job id as a
    stored one. That risks skipping a job; the alternative miscounts one on
    every poll.
    """
    job_id = _number(attributes, "job-id")

    uuid = _text(attributes, "job-uuid")
    if uuid and not uuid.endswith(_ZEROED_UUID_SUFFIX):
        return uuid

    if "time-at-creation" in attributes:
        return f"uptime-{_number(attributes, 'time-at-creation')}#{job_id}"

    created_at = _moment(attributes, "date-time-at-creation")
    if created_at is not None:
        return f"{created_at.isoformat()}#{job_id}"

    return f"job-{job_id}"


@dataclass(frozen=True, slots=True)
class Job:
    """One print job, with the page counts the printer actually charged for.

    :mod:`ipp_joblog.store` derives its column list from these fields. The last
    two are not displayed anywhere: they are the raw inputs :func:`job_key` uses,
    kept so that a future change to how keys are derived can recompute existing
    rows instead of stranding them under their old keys.
    """

    key: str
    job_id: int
    job_name: str
    user_name: str
    state: str
    impressions: int
    sheets: int | None
    color_mode: str
    sides: str
    document_format: str
    created_at: datetime | None
    completed_at: datetime | None
    job_uuid: str = ""
    time_at_creation: int = 0

    @property
    def is_color(self) -> bool:
        return self.color_mode not in MONOCHROME_MODES

    @classmethod
    def from_attributes(cls, attributes: dict[str, Attribute]) -> Job:
        """Build a job from one IPP job-attributes group."""
        return cls(
            key=job_key(attributes),
            job_id=_number(attributes, "job-id"),
            job_name=_text(attributes, "job-name"),
            user_name=_text(attributes, "job-originating-user-name"),
            state=JOB_STATES.get(_number(attributes, "job-state"), "unknown"),
            impressions=_number(attributes, "job-impressions-completed"),
            sheets=_optional_number(attributes, "job-media-sheets-completed"),
            color_mode=_text(attributes, "print-color-mode"),
            sides=_text(attributes, "sides"),
            document_format=_text(attributes, "document-format-supplied"),
            created_at=_moment(attributes, "date-time-at-creation"),
            completed_at=_moment(attributes, "date-time-at-completed"),
            job_uuid=_text(attributes, "job-uuid"),
            time_at_creation=_number(attributes, "time-at-creation"),
        )


class PrinterJobLog:
    """Reads the printer's finished-job history over IPP."""

    def __init__(
        self, host: str, *, timeout: float = 20.0, port: int = 631, path: str | None = None
    ) -> None:
        self.client = IppClient(host, timeout=timeout, port=port, path=path)

    @classmethod
    def using(cls, client: IppClient) -> PrinterJobLog:
        """Wrap a client whose endpoint is already settled, so discovery happens once."""
        log = cls.__new__(cls)
        log.client = client
        return log

    def finished_jobs(self, *, limit: int = 500) -> list[Job]:
        """Every job the printer still remembers, oldest first."""
        groups = self.client.get_jobs(which_jobs="completed", limit=limit)
        jobs = [Job.from_attributes(group) for group in groups]
        epoch = datetime.min.replace(tzinfo=UTC)
        return sorted(jobs, key=lambda job: job.created_at or epoch)
