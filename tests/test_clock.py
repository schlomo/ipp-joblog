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


def test_poll_reports_the_skew_and_the_likely_cause(tmp_path, make_job, monkeypatch, capsys):
    from ipp_joblog.store import JobStore

    ahead = datetime.now().astimezone() + timedelta(minutes=60)

    class Unreachable:
        """Reading printer details fails; collecting page counts must not."""

        def printer_attributes(self):
            raise OSError("no details today")

    class FakeLog:
        client = Unreachable()

        def finished_jobs(self, *, limit: int = 500):
            return [make_job(completed_at=ahead)]

    monkeypatch.setattr("ipp_joblog.cli.connect", lambda settings, **kw: object())
    monkeypatch.setattr(
        "ipp_joblog.cli.PrinterJobLog.using", staticmethod(lambda client: FakeLog())
    )
    monkeypatch.setattr("ipp_joblog.cli.collect_facts", lambda client, host: {"host": host})
    assert main(["--state-dir", str(tmp_path), "--host", "printer.example", "poll"]) == 0
    err = capsys.readouterr().err
    assert "60 minutes ahead" in err
    assert "daylight saving" in err  # the cause a correct-looking clock hides
    assert JobStore(tmp_path / "printer.example.sqlite3").rows()  # the job is still kept
