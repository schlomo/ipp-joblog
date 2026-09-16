from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from ipp_joblog.cli import build_parser, main, parse_moment, report_poll
from ipp_joblog.ipp import COMMON_PATHS, Attempt, IppError
from ipp_joblog.jobs import Job
from ipp_joblog.poller import Poller
from ipp_joblog.store import SCHEMA_VERSION, JobStore


class FakeLog:
    """Stands in for the printer: returns a scripted history per poll."""

    def __init__(self, histories: list[list[Job]]) -> None:
        self._histories = histories
        self.polls = 0

    def finished_jobs(self, *, limit: int = 500) -> list[Job]:
        history = self._histories[min(self.polls, len(self._histories) - 1)]
        self.polls += 1
        return history


def test_poll_stores_only_new_jobs(store, make_job):
    first, second = make_job(key="a"), make_job(key="b", job_id=20)
    poller = Poller(FakeLog([[first], [first, second]]), store)
    assert poller.poll().stored == [first]
    assert poller.poll().stored == [second]
    assert len(store.rows()) == 2


def test_poll_flags_a_rolled_over_history(store, make_job):
    poller = Poller(FakeLog([[make_job(key="a")], [make_job(key="z", job_id=99)]]), store)
    assert poller.poll().rolled_over is False  # nothing stored yet to lose
    assert poller.poll().rolled_over is True


def test_report_poll_logs_jobs_to_stderr_not_stdout(store, make_job, capsys):
    """stdout carries data only, so poll can be piped without log noise."""
    report_poll(Poller(FakeLog([[make_job(key="a", user_name="alice")]]), store))
    captured = capsys.readouterr()
    assert "alice" in captured.err
    assert captured.out == ""


def test_report_poll_warns_on_rollover(store, make_job, capsys):
    poller = Poller(FakeLog([[make_job(key="a")], [make_job(key="z", job_id=99)]]), store)
    report_poll(poller)
    assert "rolled over" not in capsys.readouterr().err
    report_poll(poller)
    assert "rolled over" in capsys.readouterr().err


def test_parse_moment_accepts_age_shorthand():
    now = datetime.now().astimezone()
    assert abs((now - parse_moment("30d")) - timedelta(days=30)) < timedelta(seconds=5)
    assert abs((now - parse_moment("12h")) - timedelta(hours=12)) < timedelta(seconds=5)


def test_parse_moment_accepts_iso_and_adds_local_zone():
    assert parse_moment("2026-09-10T20:45:28+01:00").hour == 20
    assert parse_moment("2026-09-10").tzinfo is not None


def test_poll_requires_a_host(monkeypatch, capsys):
    monkeypatch.delenv("IPP_PRINTER_HOST", raising=False)
    parser_host = build_parser().parse_args(["poll"]).host
    if parser_host:  # a host is configured in this environment; nothing to assert
        pytest.skip("IPP_PRINTER_HOST is set in this environment")
    with pytest.raises(SystemExit):
        main(["poll"])
    assert "IPP_PRINTER_HOST" in capsys.readouterr().err


def test_report_does_not_need_a_host(tmp_path, monkeypatch):
    monkeypatch.delenv("IPP_PRINTER_HOST", raising=False)
    assert main(["--state-dir", str(tmp_path), "--host", "printer.example", "report"]) == 0


def test_report_formats(tmp_path, make_job, capsys):
    path = tmp_path / "printer.example.sqlite3"
    with JobStore(path) as store:
        store.add([make_job(user_name="alice", impressions=72, sheets=36)])

    assert main(["--state-dir", str(path.parent), "--host", "printer.example", "report"]) == 0
    assert "alice" in capsys.readouterr().out

    assert (
        main(
            [
                "--state-dir",
                str(path.parent),
                "--host",
                "printer.example",
                "report",
                "--format",
                "csv",
            ]
        )
        == 0
    )
    csv_out = capsys.readouterr().out
    assert "user_name,jobs,impressions,sheets,color_impressions,mono_impressions" in csv_out
    assert "alice,1,72,36,0,72" in csv_out

    assert (
        main(
            [
                "--state-dir",
                str(path.parent),
                "--host",
                "printer.example",
                "report",
                "--format",
                "json",
            ]
        )
        == 0
    )
    assert '"impressions": 72' in capsys.readouterr().out


def test_jobs_command_emits_csv_with_header(tmp_path, make_job, capsys):
    path = tmp_path / "printer.example.sqlite3"
    with JobStore(path) as store:
        store.add([make_job()])
    assert main(["--state-dir", str(path.parent), "--host", "printer.example", "jobs"]) == 0
    output = capsys.readouterr().out.splitlines()
    assert output[0].startswith("key,job_id,job_name,user_name")
    assert "alice" in output[1]


class StubProbeClient:
    """Minimal stand-in for IppClient, driven by a scripted job history."""

    def __init__(self, groups, *, working_path="/ipp/print", **_) -> None:
        self.groups = groups
        self.path = "/ipp/print"
        self.working_path = working_path
        self.host = "printer.example"
        self.port = 631

    @property
    def printer_uri(self) -> str:
        return f"ipp://{self.host}:{self.port}{self.path}"

    def find_endpoint(self, paths=COMMON_PATHS, ports=(631,), on_attempt=None):
        for candidate in paths:
            found = candidate == self.working_path
            if on_attempt:
                on_attempt(Attempt(self.port, False, candidate, None if found else "HTTP 404"))
            if found:
                self.path = candidate
                return Attempt(self.port, False, candidate)
        raise IppError("no IPP endpoint answered")

    def printer_attributes(self):
        return {}

    def get_jobs(self, **_):
        return self.groups


def _probe(monkeypatch, groups, argv, **kwargs):
    monkeypatch.setattr(
        "ipp_joblog.cli.IppClient", lambda *a, **k: StubProbeClient(groups, **kwargs)
    )
    # No DNS from the tests; the scan table is not what these assert on.
    monkeypatch.setattr(
        "ipp_joblog.diagnose.scan",
        lambda host, timeout: dict.fromkeys((631, 80, 443, 9100, 515), "open"),
    )
    return main(argv)


def test_probe_lists_every_url_it_tries(monkeypatch, attributes, capsys):
    group = attributes(job_id=1, job_originating_user_name="alice", job_impressions_completed=2)
    assert (
        _probe(
            monkeypatch, [group], ["--host", "printer.example", "probe"], working_path="/ipp/port1"
        )
        == 0
    )
    captured = capsys.readouterr()
    assert "scanning printer.example" in captured.err
    # One line per port rather than one per path: five paths is not five lines.
    assert "631         answered IPP at /ipp/port1" in captured.err
    assert "found IPP at ipp://printer.example:631/ipp/port1" in captured.err
    assert "can be accounted for" in captured.out  # the verdict is the data


def test_a_full_address_skips_discovery(monkeypatch, attributes, capsys):
    """A URL answers both questions, so there is nothing left to scan for."""
    group = attributes(job_id=1, job_originating_user_name="alice", job_impressions_completed=2)
    _probe(monkeypatch, [group], ["--host", "ipp://printer.example/ipp/print", "probe"])
    captured = capsys.readouterr()
    assert "using IPP endpoint (given)" in captured.err
    assert "fail" not in captured.err


def test_what_counts_as_a_complete_address():
    from ipp_joblog.cli import Settings

    def target(host, path=None):
        return Settings(host=host, state_dir=".", timeout=1, path=path).target

    assert target("printer").pinned is False  # nothing known
    assert target("printer:80").pinned is False  # port only
    assert target("printer", path="/ipp/print").pinned is False  # path only
    assert target("ipp://printer/ipp/print").pinned is True
    assert target("printer:80", path="/ipp/print").pinned is True  # --path still works


def test_the_database_is_named_after_the_host_not_the_url(tmp_path):
    """However the address is written, one printer keeps one database."""
    from ipp_joblog.cli import Settings

    def database(host):
        return Settings(host=host, state_dir=tmp_path, timeout=1, path=None).database.name

    assert database("hpm880") == "hpm880.sqlite3"
    assert database("ipp://hpm880:631/ipp/print") == "hpm880.sqlite3"
    assert database("hpm880:80") == "hpm880.sqlite3"


def test_probe_fails_when_the_printer_keeps_no_history(monkeypatch, capsys):
    assert _probe(monkeypatch, [], ["--host", "printer.example", "probe"]) == 1
    assert "keeps no completed-job history" in capsys.readouterr().out


def test_probe_fails_when_a_required_attribute_is_missing(monkeypatch, attributes, capsys):
    group = attributes(job_id=1, job_impressions_completed=2)  # no user name
    assert _probe(monkeypatch, [group], ["--host", "printer.example", "probe"]) == 1
    captured = capsys.readouterr()
    assert "NO   job-originating-user-name" in captured.out
    assert "Missing required attribute" in captured.out


def test_probe_report_is_usable_when_everything_is_present(attributes):
    from ipp_joblog.probe import probe

    group = attributes(job_id=1, job_originating_user_name="alice", job_impressions_completed=2)
    report = probe(StubProbeClient([group]))
    assert report.usable is True
    assert report.problem() == ""
    assert report.retained_jobs == 1


def test_probe_report_explains_an_empty_history():
    from ipp_joblog.probe import probe

    report = probe(StubProbeClient([]))
    assert report.usable is False
    assert "keeps no completed-job history" in report.problem()
    assert "retained completed jobs: 0" in "\n".join(report.lines())


def test_probe_report_names_the_missing_attribute(attributes):
    from ipp_joblog.probe import probe

    report = probe(StubProbeClient([attributes(job_id=1, job_impressions_completed=2)]))
    assert report.missing_required == ["job-originating-user-name"]
    assert report.usable is False
    assert "NO   job-originating-user-name  (required)" in "\n".join(report.lines())


def test_a_new_database_is_announced_on_stderr(tmp_path, capsys):
    path = tmp_path / "printer.example.sqlite3"
    assert main(["--state-dir", str(path.parent), "--host", "printer.example", "report"]) == 0
    captured = capsys.readouterr()
    assert f"created {path} at schema v{SCHEMA_VERSION}" in captured.err
    assert "created" not in captured.out  # stdout is the report, nothing else


def test_an_upgraded_database_is_announced_on_stderr(tmp_path, capsys):
    import sqlite3

    from ipp_joblog.store import CREATE_JOBS

    path = tmp_path / "printer.example.sqlite3"
    legacy = sqlite3.connect(path)
    legacy.execute(CREATE_JOBS)
    legacy.commit()
    legacy.close()

    assert main(["--state-dir", str(path.parent), "--host", "printer.example", "report"]) == 0
    captured = capsys.readouterr()
    assert f"upgraded {path} to schema v{SCHEMA_VERSION}" in captured.err
    assert "applied v1" in captured.err  # a pre-migration file runs them all
    assert captured.out.startswith("user")


def test_an_up_to_date_database_says_nothing(tmp_path, capsys):
    path = tmp_path / "printer.example.sqlite3"
    main(["--state-dir", str(path.parent), "--host", "printer.example", "report"])
    capsys.readouterr()
    assert main(["--state-dir", str(path.parent), "--host", "printer.example", "report"]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""


def test_report_output_is_clean_stdout(tmp_path, make_job, capsys):
    """`report --format csv > file` must capture exactly the CSV."""
    path = tmp_path / "printer.example.sqlite3"
    with JobStore(path) as store:
        store.add([make_job(user_name="alice", impressions=72, sheets=36)])
    capsys.readouterr()
    assert (
        main(
            [
                "--state-dir",
                str(path.parent),
                "--host",
                "printer.example",
                "report",
                "--format",
                "csv",
            ]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert out.splitlines()[0].startswith("user_name,")
    assert len(out.splitlines()) == 2


def test_notices_do_not_overtake_what_was_already_printed(monkeypatch):
    """Whatever is already on stdout goes out before the next line of stderr."""
    import sys

    from ipp_joblog.cli import notice

    order: list[str] = []

    class Recording:
        def write(self, text):
            return len(text)

        def flush(self):
            order.append("stdout flushed")

    monkeypatch.setattr(sys, "stdout", Recording())
    monkeypatch.setattr(
        sys,
        "stderr",
        type(
            "E",
            (),
            {"write": lambda self, t: len(t), "flush": lambda self: order.append("stderr written")},
        )(),
    )
    notice("progress")
    assert order == ["stdout flushed", "stderr written"]


def test_stdout_is_line_buffered_so_it_keeps_step_with_stderr(monkeypatch, tmp_path):
    """Block-buffered stdout would let an answer lag behind the progress for it."""
    import sys

    asked: dict[str, object] = {}

    class Stdout:
        def reconfigure(self, **kwargs):
            asked.update(kwargs)

        def write(self, text):
            return len(text)

        def flush(self):
            return None

    monkeypatch.setattr(sys, "stdout", Stdout())
    main(["--state-dir", str(tmp_path), "report"])
    assert asked == {"line_buffering": True}


def test_a_stdout_that_cannot_be_reconfigured_is_not_fatal(monkeypatch, tmp_path, make_job):
    """Not every stdout has reconfigure: pytest's capture replaces it, and so do pipes."""
    import sys

    JobStore(tmp_path / "printer.sqlite3").add([make_job()])

    class Plain:  # no reconfigure at all
        def write(self, text):
            return len(text)

        def flush(self):
            return None

    monkeypatch.setattr(sys, "stdout", Plain())
    assert main(["--state-dir", str(tmp_path), "report"]) == 0
