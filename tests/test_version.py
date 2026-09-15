from __future__ import annotations

import subprocess
import sys

from ipp_joblog.version import FALLBACK, resolve


def test_environment_wins(monkeypatch):
    """The container image has no git and no metadata, so it passes the version in."""
    monkeypatch.setenv("IPP_JOBLOG_VERSION", "1.0.42")
    assert resolve() == "1.0.42"


def test_falls_back_to_installed_metadata(monkeypatch):
    monkeypatch.delenv("IPP_JOBLOG_VERSION", raising=False)
    assert resolve() != FALLBACK  # the test run has the package installed


def test_cli_reports_a_version():
    output = subprocess.run(
        [sys.executable, "-m", "ipp_joblog", "--version"],
        capture_output=True,
        text=True,
        check=True,
        env={"IPP_JOBLOG_VERSION": "1.0.42", "PATH": "/usr/bin:/bin"},
    ).stdout
    assert "1.0.42" in output
