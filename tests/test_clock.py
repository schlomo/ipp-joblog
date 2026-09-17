"""A printer whose reported times cannot be true."""

from __future__ import annotations

from datetime import datetime, timedelta

from ipp_joblog.cli import main
from ipp_joblog.poller import PollResult


def test_a_job_finishing_in_our_future_is_impossible(make_job):
    """The evidence that the offset is wrong: we saw it before it happened."""
    ahead = datetime.now().astimezone() + timedelta(minutes=60)
    result = PollResult(stored=[make_job(completed_at=ahead)], rolled_over=False)
    assert result.clock_skew is not None
    assert 59 <= result.clock_skew.total_seconds() / 60 <= 61


def test_ordinary_drift_is_not_worth_saying(make_job):
    slight = datetime.now().astimezone() + timedelta(seconds=30)
    assert PollResult(stored=[make_job(completed_at=slight)], rolled_over=False).clock_skew is None


def test_a_printer_running_slow_is_not_detectable(make_job):
    """Only "ahead" is provable; behind just looks like old news."""
    behind = datetime.now().astimezone() - timedelta(hours=3)
    assert PollResult(stored=[make_job(completed_at=behind)], rolled_over=False).clock_skew is None


def test_no_jobs_means_nothing_to_judge():
    assert PollResult(stored=[], rolled_over=False).clock_skew is None


def test_no_printer_clock_means_no_correction_is_invented(tmp_path, make_job, monkeypatch, capsys):
    """A printer that does not report its clock is left uncorrected, not guessed at."""
    from ipp_joblog.store import JobStore

    ahead = datetime.now().astimezone() + timedelta(minutes=60)

    class FakeLog:
        client = None

        def finished_jobs(self, *, limit: int = 500):
            return [make_job(completed_at=ahead)]

    monkeypatch.setattr("ipp_joblog.cli.connect", lambda settings, **kw: object())
    monkeypatch.setattr(
        "ipp_joblog.cli.PrinterJobLog.using", staticmethod(lambda client: FakeLog())
    )
    # collect_facts returns no clock fact, as a printer without printer-current-time would.
    monkeypatch.setattr("ipp_joblog.cli.collect_facts", lambda client, host: {"host": host})
    assert main(["--state-dir", str(tmp_path), "--host", "printer.example", "poll"]) == 0

    store = JobStore(tmp_path / "printer.example.sqlite3")
    assert store.rows()  # the job is kept
    assert store.clock_correction() == timedelta()  # but nothing is corrected on a guess


def test_correction_rounds_to_a_time_zone_step_and_ignores_drift():
    from ipp_joblog.clock import correction_for

    assert correction_for(timedelta(minutes=60)) == timedelta(minutes=-60)  # DST bug
    assert correction_for(timedelta(minutes=58)) == timedelta(minutes=-60)  # measured loosely
    assert correction_for(timedelta(minutes=30)) == timedelta(minutes=-30)  # half-hour zone
    assert correction_for(timedelta(minutes=45)) == timedelta()  # not a clean step: drift
    assert correction_for(timedelta(minutes=7)) == timedelta()  # ordinary drift
    assert correction_for(timedelta()) == timedelta()  # a right clock
    assert correction_for(None) == timedelta()  # nothing measured


def test_a_correct_printer_is_never_touched():
    """The correction is measured, so a printer with the right offset gets zero."""
    from ipp_joblog.clock import apply_iso, correction_for

    correction = correction_for(timedelta(seconds=3))
    assert correction == timedelta()
    assert apply_iso("2026-09-17T07:50:00+02:00", correction) == "2026-09-17T07:50:00+02:00"


def test_printer_current_time_sets_the_correction_at_startup(tmp_path):
    """The printer's own clock, read against ours, is the exact offset."""
    from ipp_joblog.ipp import Attribute
    from ipp_joblog.probe import collect_facts
    from ipp_joblog.store import JobStore

    # A printer whose clock reads an hour ahead of the real instant, right now.
    printer_now = datetime.now().astimezone() + timedelta(minutes=60)

    class Client:
        printer_uri = "ipp://hpm880:631/ipp/print"

        def printer_attributes(self):
            return {"printer-current-time": Attribute(0x31, "printer-current-time", [printer_now])}

    with JobStore(tmp_path / "hpm880.sqlite3") as store:
        store.remember_facts(collect_facts(Client(), "hpm880"))
        assert store.clock_correction() == timedelta(minutes=-60)


def test_the_dashboard_shows_corrected_times(make_job):
    from datetime import timezone

    from ipp_joblog.page import Dashboard

    job = {
        "completed_at": "2026-09-17T08:50:00+02:00",
        "user_name": "schlomo",
        "impressions": 2,
        "sheets": 1,
        "color_mode": "monochrome",
        "state": "completed",
        "job_name": "Rechnung",
    }
    board = Dashboard(
        printer="hpm880",
        generated_at=datetime(2026, 9, 17, 7, 51, tzinfo=timezone(timedelta(hours=2))),
        recent_jobs=[job],
        correction=timedelta(minutes=-60),
    )
    html = board.render()
    assert "17 Sep 07:50" in html  # shifted back from the reported 08:50
    assert "08:50" not in html
