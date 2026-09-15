"""One poll: read the printer's history, keep what is new, notice what was lost."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from ipp_joblog.jobs import Job, PrinterJobLog
from ipp_joblog.store import JobStore

# Clocks drift; an hour is not drift. Below this, say nothing.
SKEW_TOLERANCE = timedelta(minutes=2)


@dataclass(frozen=True, slots=True)
class PollResult:
    """What one poll changed."""

    stored: list[Job]
    rolled_over: bool

    def __len__(self) -> int:
        return len(self.stored)

    @property
    def clock_skew(self) -> timedelta | None:
        """How far ahead of us the printer's clock is, if provably wrong.

        A job cannot finish after the moment we read it, so a completion time in
        our future means the printer's clock or its declared UTC offset is
        wrong, and every timestamp it reports will be too. Only "ahead" is
        detectable this way: a printer running slow just looks like old news.
        """
        now = datetime.now().astimezone()
        ahead = [
            job.completed_at - now
            for job in self.stored
            if job.completed_at and job.completed_at - now > SKEW_TOLERANCE
        ]
        return max(ahead) if ahead else None


@dataclass(frozen=True, slots=True)
class Poller:
    """Folds the printer's short job history into the durable store."""

    log: PrinterJobLog
    store: JobStore

    def poll(self) -> PollResult:
        jobs = self.log.finished_jobs()
        # Ask before storing: afterwards every job in the snapshot is known.
        rolled_over = self.store.is_rollover(jobs)
        return PollResult(stored=self.store.add(jobs), rolled_over=rolled_over)
