"""What a printer that cannot be accounted for should produce."""

from __future__ import annotations

import pytest

from ipp_joblog.cli import main
from ipp_joblog.diagnose import ISSUES, reachability, unreachable_report


@pytest.fixture
def refused(monkeypatch):
    """An address that answers, with nothing listening: TCP RST on every port."""
    monkeypatch.setattr("ipp_joblog.ipp.port_state", lambda *a, **k: "refused")
    monkeypatch.setattr("ipp_joblog.diagnose.port_state", lambda *a, **k: "refused")


@pytest.fixture
def silent(monkeypatch):
    monkeypatch.setattr("ipp_joblog.ipp.port_state", lambda *a, **k: "no answer")
    monkeypatch.setattr("ipp_joblog.diagnose.port_state", lambda *a, **k: "no answer")


def states(monkeypatch, open_ports):
    """Pretend a particular set of ports is open and the rest refuse."""
    monkeypatch.setattr(
        "ipp_joblog.diagnose.port_state",
        lambda host, port, timeout: "open" if port in open_ports else "refused",
    )


def test_raw_printing_without_ipp_is_the_whole_answer(monkeypatch):
    """A printer that takes bytes on 9100 but no IPP keeps no job history to read."""
    states(monkeypatch, {9100, 80})
    text = "\n".join(reachability("192.0.2.10", timeout=1))
    assert "9100  open" in text
    assert "no per-user" in text and "job history" in text
    assert "cannot help with" in text  # said plainly rather than left to infer
    assert "web interface is open" in text  # ...but IPP may just be switched off


def test_lpd_counts_as_raw_printing_too(monkeypatch):
    states(monkeypatch, {515})
    assert "job history" in "\n".join(reachability("192.0.2.10", timeout=1))


def test_ipp_closed_but_web_open_suggests_a_setting(monkeypatch):
    states(monkeypatch, {80})
    text = "\n".join(reachability("192.0.2.10", timeout=1))
    assert "disabled there" in text
    assert "job history" not in text  # nothing suggests raw printing here


def test_every_port_refused_means_it_is_probably_not_the_printer(refused):
    """An EPSON L3150 refused all IPP ports while printing happily over CUPS."""
    text = "\n".join(reachability("192.0.2.10", timeout=1))
    assert "631   refused" in text
    assert "probably not the printer" in text
    assert "may have moved" in text  # Wi-Fi printer on DHCP


def test_a_silent_host_is_a_different_problem(silent):
    text = "\n".join(reachability("192.0.2.10", timeout=1))
    assert "does not speak IPP" not in text
    assert "firewall" in text


def test_an_unknown_name_says_so(monkeypatch):
    monkeypatch.setattr("ipp_joblog.diagnose.port_state", lambda *a, **k: "unknown host")
    assert "does not resolve" in "\n".join(reachability("nope.invalid", timeout=1))


def test_the_unreachable_report_lists_what_was_tried(refused):
    text = "\n".join(unreachable_report("192.0.2.10", timeout=1))
    assert "/ipp/print" in text
    assert "443" in text and "9100" in text


def test_probe_diagnoses_itself_when_nothing_answers(refused, capsys, tmp_path):
    """The reporter should not have to be told to run a second command."""
    assert main(["--state-dir", str(tmp_path), "--host", "192.0.2.10", "probe"]) == 1
    captured = capsys.readouterr()
    assert "no IPP endpoint answered" in captured.err
    assert "631   refused" in captured.out
    assert ISSUES in captured.out
    assert "diagnose --watch 90" in captured.out
