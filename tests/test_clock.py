"""Correcting printers that report the wrong clock, at the moment jobs are stored."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ipp_joblog.clock import apply, correction_for, measure
from ipp_joblog.ipp import Attribute
from ipp_joblog.store import JobStore

CET = timezone(timedelta(hours=1))
CEST = timezone(timedelta(hours=2))


def test_correction_rounds_to_a_time_zone_step_and_ignores_drift():
    assert correction_for(timedelta(minutes=60)) == timedelta(minutes=-60)  # DST bug
    assert correction_for(timedelta(minutes=58)) == timedelta(minutes=-60)  # measured loosely
    assert correction_for(timedelta(minutes=30)) == timedelta(minutes=-30)  # half-hour zone
    assert correction_for(timedelta(minutes=45)) == timedelta()  # not a clean step: drift
    assert correction_for(timedelta(minutes=7)) == timedelta()  # ordinary drift
    assert correction_for(timedelta()) == timedelta()  # a right clock
    assert correction_for(None) == timedelta()  # nothing measured


def test_a_correct_printer_is_never_shifted():
    assert correction_for(timedelta(seconds=3)) == timedelta()
    moment = datetime(2026, 9, 17, 7, 50, tzinfo=CEST)
    assert apply(moment, correction_for(timedelta(seconds=3))) == moment


class _Client:
    def __init__(self, printer_now: datetime | None):
        self._now = printer_now

    def printer_attributes(self) -> dict:
        if self._now is None:
            return {}
        return {"printer-current-time": Attribute(0x31, "printer-current-time", [self._now])}


def test_measure_reads_the_printer_clock_against_ours():
    printer_now = datetime.now().astimezone() + timedelta(minutes=60)
    ahead = measure(_Client(printer_now))
    assert ahead is not None
    assert timedelta(minutes=59) < ahead < timedelta(minutes=61)


def test_measure_is_none_when_the_printer_will_not_say():
    assert measure(_Client(None)) is None


def test_a_job_is_stored_at_the_true_instant_with_the_raw_kept(tmp_path, make_job):
    """completed_at is corrected; the printer's literal value stays in the raw column."""
    reported = datetime(2026, 9, 17, 8, 50, tzinfo=CET)  # an hour ahead of the truth
    with JobStore(tmp_path / "hpm880.sqlite3") as store:
        store.add([make_job(completed_at=reported)], correction=timedelta(minutes=-60))
        row = store.rows()[0]
    assert row["completed_at"] == "2026-09-17T07:50:00+01:00"  # shifted back an hour
    assert row["completed_at_raw"] == "2026-09-17T08:50:00+01:00"  # exactly as sent


def test_each_job_keeps_the_correction_true_when_it_was_stored(tmp_path, make_job):
    """A later daylight-saving change does not reach back and skew the archive."""
    summer = datetime(2026, 8, 1, 12, 0, tzinfo=CET)
    with JobStore(tmp_path / "hpm880.sqlite3") as store:
        # stored during summer, corrected by an hour
        store.add([make_job(key="a", completed_at=summer)], correction=timedelta(minutes=-60))
        # a later job, printer since fixed, stored with no correction
        winter = datetime(2026, 11, 1, 12, 0, tzinfo=CET)
        store.add([make_job(key="b", completed_at=winter)], correction=timedelta())
        rows = {r["key"]: r for r in store.rows()}
    assert rows["a"]["completed_at"] == "2026-08-01T11:00:00+01:00"  # corrected
    assert rows["b"]["completed_at"] == "2026-11-01T12:00:00+01:00"  # left as sent


def test_a_printer_that_reports_its_clock_corrects_new_jobs(tmp_path, make_job):
    """End to end through the poller: measure, correct, store the true instant."""
    from ipp_joblog.poller import Poller

    printer_now = datetime.now().astimezone() + timedelta(minutes=60)
    reported = datetime(2026, 9, 17, 8, 50, tzinfo=CET)

    class Log:
        client = _Client(printer_now)

        def finished_jobs(self, *, limit: int = 500):
            return [make_job(completed_at=reported)]

    with JobStore(tmp_path / "hpm880.sqlite3") as store:
        result = Poller(Log(), store).poll()
        assert result.correction == timedelta(minutes=-60)
        assert store.rows()[0]["completed_at"] == "2026-09-17T07:50:00+01:00"


def test_a_printer_without_a_clock_stores_times_as_sent(tmp_path, make_job):
    from ipp_joblog.poller import Poller

    reported = datetime(2026, 9, 17, 8, 50, tzinfo=CET)

    class Log:
        client = _Client(None)  # no printer-current-time

        def finished_jobs(self, *, limit: int = 500):
            return [make_job(completed_at=reported)]

    with JobStore(tmp_path / "hpm880.sqlite3") as store:
        result = Poller(Log(), store).poll()
        assert result.correction == timedelta()
        assert store.rows()[0]["completed_at"] == "2026-09-17T08:50:00+01:00"  # untouched


def test_the_dashboard_shows_the_stored_corrected_time():
    from ipp_joblog.page import Dashboard

    job = {
        "completed_at": "2026-09-17T07:50:00+01:00",  # already corrected in the store
        "user_name": "schlomo",
        "impressions": 2,
        "sheets": 1,
        "color_mode": "monochrome",
        "state": "completed",
        "job_name": "Rechnung",
    }
    html = Dashboard(
        printer="hpm880",
        generated_at=datetime(2026, 9, 17, 8, 51, tzinfo=CEST),
        recent_jobs=[job],
    ).render()
    assert "17 Sep 08:50" in html  # 07:50+01:00 shown in CEST
