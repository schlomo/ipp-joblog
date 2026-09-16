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


def test_refused_everywhere_points_at_the_print_queue(refused):
    """An EPSON L3150 refused all IPP ports while printing happily over CUPS."""
    text = "\n".join(reachability("192.0.2.10", timeout=1))
    assert "port 631   refused" in text
    assert "does not speak IPP" in text
    assert "lpstat -v" in text  # how to find out what it does speak
    assert "socket://" in text and "usb://" in text  # and what those answers mean


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
    assert "port 443" in text


def test_probe_diagnoses_itself_when_nothing_answers(refused, capsys, tmp_path):
    """The reporter should not have to be told to run a second command."""
    assert main(["--state-dir", str(tmp_path), "--host", "192.0.2.10", "probe"]) == 1
    captured = capsys.readouterr()
    assert "no IPP endpoint answered" in captured.err
    assert "port 631   refused" in captured.out
    assert ISSUES in captured.out
    assert "diagnose --watch 90" in captured.out
