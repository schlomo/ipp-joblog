"""One poll: read the printer's history, keep what is new, notice what was lost."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from ipp_joblog import clock
from ipp_joblog.jobs import Job, PrinterJobLog
from ipp_joblog.store import JobStore


@dataclass(frozen=True, slots=True)
class PollResult:
    """What one poll changed."""

    stored: list[Job]
    rolled_over: bool
    #: The clock correction applied to the jobs stored this poll, if any.
    correction: timedelta = timedelta()

    def __len__(self) -> int:
        return len(self.stored)


@dataclass(frozen=True, slots=True)
class Poller:
    """Folds the printer's short job history into the durable store."""

    log: PrinterJobLog
    store: JobStore

    def poll(self) -> PollResult:
        jobs = self.log.finished_jobs()
        # Ask before storing: afterwards every job in the snapshot is known.
        rolled_over = self.store.is_rollover(jobs)

        # Measure the printer's clock only when there is something new to store,
        # so each job is corrected by the offset that was true when it printed
        # -- which is what lets a daylight-saving change look after itself.
        known = self.store.known_keys()
        correction = timedelta()
        if any(job.key not in known for job in jobs):
            correction = clock.correction_for(clock.measure(self.log.client))

        stored = self.store.add(jobs, correction=correction)
        return PollResult(stored=stored, rolled_over=rolled_over, correction=correction)
