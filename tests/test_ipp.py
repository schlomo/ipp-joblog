from __future__ import annotations

import struct
from datetime import datetime, timedelta, timezone

import pytest

from ipp_joblog.ipp import (
    COMMON_PATHS,
    IPP_VERSION,
    IppClient,
    IppError,
    Operation,
    Tag,
    _decode_datetime,
    decode_response,
    encode_request,
)


def test_encode_request_header():
    body = encode_request(Operation.GET_JOBS, 7, [])
    assert body[:2] == bytes(IPP_VERSION)
    assert struct.unpack(">H", body[2:4])[0] == Operation.GET_JOBS
    assert struct.unpack(">I", body[4:8])[0] == 7
    assert body[8] == Tag.OPERATION_ATTRIBUTES
    assert body[-1] == Tag.END_OF_ATTRIBUTES


def test_encode_request_attribute_layout():
    body = encode_request(Operation.GET_JOBS, 1, [(Tag.KEYWORD, "which-jobs", ["completed"])])
    assert body[9] == Tag.KEYWORD
    assert body[10:12] == struct.pack(">H", len("which-jobs"))
    assert b"which-jobs" in body
    assert b"completed" in body


def test_encode_request_integer_is_four_octets():
    body = encode_request(Operation.GET_JOBS, 1, [(Tag.INTEGER, "limit", [500])])
    tail = struct.pack(">H", 4) + struct.pack(">i", 500) + bytes([Tag.END_OF_ATTRIBUTES])
    assert body.endswith(tail)


def test_encode_request_additional_values_have_empty_names():
    body = encode_request(
        Operation.GET_JOBS, 1, [(Tag.KEYWORD, "requested-attributes", ["job-id", "job-name"])]
    )
    assert body.count(struct.pack(">H", 0) + struct.pack(">H", len("job-name"))) == 1


def test_decode_datetime_roundtrip():
    raw = struct.pack(">HBBBBBB", 2026, 9, 10, 22, 39, 30, 6) + b"+" + bytes([1, 0])
    assert _decode_datetime(raw) == datetime(
        2026, 9, 10, 22, 39, 30, 600000, timezone(timedelta(hours=1))
    )


def test_decode_datetime_negative_offset():
    raw = struct.pack(">HBBBBBB", 2026, 1, 2, 3, 4, 5, 0) + b"-" + bytes([5, 30])
    moment = _decode_datetime(raw)
    assert moment.utcoffset().total_seconds() == -(5 * 3600 + 30 * 60)


def test_decode_datetime_rejects_short_and_invalid():
    assert _decode_datetime(b"\x00" * 4) is None
    impossible = struct.pack(">HBBBBBB", 2026, 13, 40, 0, 0, 0, 0) + b"+\x00\x00"
    assert _decode_datetime(impossible) is None


def test_decode_response_truncated():
    with pytest.raises(IppError, match="truncated"):
        decode_response(b"\x02\x00")


def test_decode_recorded_response(get_jobs_response):
    status_code, groups = decode_response(get_jobs_response)
    assert status_code == 0x0000
    jobs = [group for group in groups if "job-id" in group]
    assert len(jobs) == 20
    assert groups[0]["attributes-charset"].value == "utf-8"


def test_decoded_job_has_the_attributes_we_report_on(job_groups):
    job = job_groups[0]
    for name in (
        "job-id",
        "job-name",
        "job-originating-user-name",
        "job-impressions-completed",
        "job-media-sheets-completed",
        "print-color-mode",
        "sides",
        "date-time-at-completed",
    ):
        assert name in job, name


def test_name_with_language_strips_language_tag(job_groups):
    users = {group["job-originating-user-name"].value for group in job_groups}
    assert "alice" in users
    assert not any("[" in user for user in users)


def test_no_value_tag_decodes_to_none(job_groups):
    assert job_groups[0]["job-impressions"].value is None


class StubClient(IppClient):
    """An IppClient whose only working endpoint is ``working_path``."""

    def __init__(self, working_path: str | None, working_port: int = 631) -> None:
        super().__init__("printer.example")
        self.working_path = working_path
        self.working_port = working_port
        self.tried: list[tuple[int, str]] = []

    def printer_attributes(self):
        self.tried.append((self.port, self.path))
        if (self.port, self.path) != (self.working_port, self.working_path):
            raise IppError("client-error-not-found")
        return {}


@pytest.fixture
def every_port_open(monkeypatch):
    """Skip the TCP check; these tests are about paths, not listeners."""
    monkeypatch.setattr("ipp_joblog.ipp.port_state", lambda *args, **kwargs: "open")


def test_find_endpoint_returns_the_first_that_answers(every_port_open):
    client = StubClient("/ipp/port1")
    found = client.find_endpoint()
    assert (found.port, found.path, found.secure) == (631, "/ipp/port1", False)
    assert (client.port, client.path) == (631, "/ipp/port1")  # left pointing at it
    assert [path for _, path in client.tried] == ["/ipp/print", "/ipp/printer", "/ipp/port1"]


def test_find_endpoint_reports_every_attempt(every_port_open):
    client = StubClient("/ipp/port1")
    seen = []
    client.find_endpoint(on_attempt=seen.append)
    assert [attempt.path for attempt in seen] == ["/ipp/print", "/ipp/printer", "/ipp/port1"]
    assert [attempt.failure is None for attempt in seen] == [False, False, True]
    assert {attempt.port for attempt in seen} == {631}


def test_find_endpoint_tries_other_ports(every_port_open):
    """Some printers answer IPP on their web port instead of 631."""
    client = StubClient("/ipp/print", working_port=80)
    assert client.find_endpoint().port == 80
    assert {port for port, _ in client.tried} == {631, 80}


def test_a_port_with_no_listener_costs_no_requests(monkeypatch):
    """A refused port is reported once, not once per candidate path."""
    monkeypatch.setattr(
        "ipp_joblog.ipp.port_state", lambda host, port, *a, **k: "open" if port == 80 else "refused"
    )
    client = StubClient("/ipp/print", working_port=80)
    seen = []
    client.find_endpoint(on_attempt=seen.append)
    assert any(a.port == 631 and a.path is None and a.failure == "refused" for a in seen)
    assert all(port == 80 for port, _ in client.tried)  # 631 never got a request


def test_a_tls_only_printer_keeps_its_scheme(every_port_open):
    """Restoring the endpoint after success would speak plain HTTP to an IPPS printer."""

    class TlsOnly(StubClient):
        def printer_attributes(self):
            self.tried.append((self.port, self.path))
            if not self.secure or self.path != "/ipp/print":
                raise IppError("client-error-not-found")
            return {}

    client = TlsOnly("/ipp/print")
    found = client.find_endpoint(ports=(631,))
    assert found.secure is True
    assert client.over_tls is True  # and it stays that way
    assert client.printer_uri == "ipps://printer.example:631/ipp/print"


def test_find_endpoint_restores_the_original_endpoint_on_failure(every_port_open):
    client = StubClient(None)
    with pytest.raises(IppError, match="no IPP endpoint answered"):
        client.find_endpoint()
    assert (client.port, client.path) == (631, COMMON_PATHS[0])


def test_port_443_is_spoken_over_tls():
    client = IppClient("printer.example", port=443)
    assert client.printer_uri.startswith("ipps://")
    assert client._http_url.startswith("https://")


def test_client_normalises_a_path_without_a_leading_slash():
    assert IppClient("printer.example", path="ipp/port1").printer_uri.endswith("/ipp/port1")


def test_tls_is_spoken_but_not_verified():
    """Every printer presents a self-signed certificate; verifying rejects them all."""
    import ssl

    from ipp_joblog.ipp import _TLS

    assert _TLS.verify_mode == ssl.CERT_NONE
    assert _TLS.check_hostname is False


def test_the_scheme_can_be_stated_rather_than_guessed():
    """IPPS on 631 is legal and a port number cannot describe it."""
    from ipp_joblog.ipp import parse_target

    stated = parse_target("ipps://printer:631/ipp/print")
    assert (stated.port, stated.secure) == (631, True)

    client = IppClient("printer", port=631, secure=True)
    assert client.printer_uri == "ipps://printer:631/ipp/print"
    assert client._http_url == "https://printer:631/ipp/print"

    assert parse_target("printer:443").secure is None  # unstated: the port decides
    assert IppClient("printer", port=443).over_tls is True


def test_tls_is_tried_only_after_plain_http():
    """Nearly every printer wants plain HTTP; TLS-only ones must still be found."""
    from ipp_joblog.ipp import _endpoint_order

    order = _endpoint_order((631, 80, 443))
    assert order[:3] == [(631, False), (80, False), (443, True)]
    assert (631, True) in order[3:]  # a printer offering only IPPS on 631


def test_a_web_page_is_not_mistaken_for_ipp():
    """A FRITZ!Box serves its own page on / and answered 200 to an IPP request.

    Decoding it ran off the end of the buffer, and the struct error that threw
    is neither IppError nor OSError, so it escaped discovery's handler and
    crashed the command instead of moving to the next candidate.
    """
    page = b"<html><head><title>FRITZ!Box</title></head><body>hi</body></html>"
    with pytest.raises(IppError, match="not an IPP response"):
        decode_response(page)


@pytest.mark.parametrize("cut", range(9, 40))
def test_no_truncation_escapes_as_something_other_than_an_ipp_error(get_jobs_response, cut):
    """Whatever a device sends, discovery must be able to move on."""
    with pytest.raises(IppError):
        decode_response(get_jobs_response[:cut])


def test_a_non_ipp_content_type_is_refused_before_decoding(monkeypatch):
    """The precise guard: an HTML answer is rejected on its headers alone."""
    import io
    from email.message import Message

    class Response(io.BytesIO):
        headers = Message()

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    page = Response(b"<html>not ipp</html>")
    page.headers["Content-Type"] = "text/html; charset=utf-8"
    monkeypatch.setattr("ipp_joblog.ipp.urlopen", lambda *a, **k: page)

    client = IppClient("printer.example")
    with pytest.raises(IppError, match="answered text/html, not IPP"):
        client.printer_attributes()
