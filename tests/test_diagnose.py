"""What a printer that cannot be accounted for should produce."""

from __future__ import annotations

import pytest

from ipp_joblog.cli import main
from ipp_joblog.diagnose import ISSUES, reachability, unreachable_report
from ipp_joblog.ipp import COMMON_PORTS


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


def test_a_printer_on_an_unusual_port_is_told_how_to_keep_using_it(monkeypatch, capsys):
    """Finding it is not enough if nothing tells the user what to configure."""
    from ipp_joblog.cli import configuration_advice
    from ipp_joblog.ipp import IppClient

    usual = IppClient("printer.example", port=631, path="/ipp/print")
    assert configuration_advice(usual) == []  # nothing to say about the default

    unusual = IppClient("printer.example", port=80, path="/ipp/printer")
    advice = "\n".join(configuration_advice(unusual))
    assert "--host ipp://printer.example:80/ipp/printer" in advice
    assert "IPP_PRINTER_HOST=ipp://printer.example:80/ipp/printer" in advice
    assert "nothing has to be configured" in advice  # discovery works; the URL just skips it

    tls = IppClient("printer.example", port=443, path="/ipp/print")
    assert "ipps://printer.example:443/ipp/print" in "\n".join(configuration_advice(tls))


def test_discovery_only_tries_ports_that_are_listening(monkeypatch, capsys):
    """The scan comes first, so a closed port costs a connection, not five requests."""
    from ipp_joblog.cli import Settings, connect

    states(monkeypatch, {80})
    monkeypatch.setattr(
        "ipp_joblog.ipp.port_state", lambda h, p, t: "open" if p == 80 else "refused"
    )

    tried: list[int] = []

    class Client:
        def __init__(self, *a, **k):
            self.port, self.path, self.host = 631, "/ipp/print", "printer.example"

        def find_endpoint(self, paths, ports, on_attempt=None):
            tried.extend(ports)
            return 80, "/ipp/print"

        @property
        def printer_uri(self):
            return "ipp://printer.example:80/ipp/print"

    monkeypatch.setattr("ipp_joblog.cli.IppClient", Client)
    connect(Settings(host="printer.example", state_dir=".", timeout=1, path=None))
    assert tried == [80]  # 631 and 443 were never asked


def test_a_sleeping_printer_is_not_written_off_by_the_scan():
    """A port that did not answer in time is not proof that nothing is there.

    A sleeping M880 answers 80 and 9100 from its network card while its IPP
    service is still waking. Excluding 631 on that evidence broke a printer
    that worked a second later.
    """
    from ipp_joblog.diagnose import worth_trying

    dozing = {631: "no answer", 80: "open", 443: "open", 9100: "open", 515: "refused"}
    assert worth_trying(dozing)[0] == 631  # tried first, not skipped

    refusing = {631: "refused", 80: "open", 443: "refused", 9100: "open", 515: "refused"}
    assert 631 not in worth_trying(refusing)  # refused is proof
    assert worth_trying(refusing) == (80,)

    assert worth_trying(dict.fromkeys((631, 80, 443, 9100, 515), "refused")) == COMMON_PORTS
