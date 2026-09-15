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

    def __init__(self, working_path: str | None) -> None:
        super().__init__("printer.example")
        self.working_path = working_path
        self.tried: list[str] = []

    def printer_attributes(self):
        self.tried.append(self.path)
        if self.path != self.working_path:
            raise IppError("client-error-not-found")
        return {}


def test_find_path_returns_the_first_endpoint_that_answers():
    client = StubClient("/ipp/port1")
    assert client.find_path() == "/ipp/port1"
    assert client.tried == ["/ipp/print", "/ipp/printer", "/ipp/port1"]


def test_find_path_reports_every_url_it_tries():
    client = StubClient("/ipp/port1")
    seen: list[tuple[str, str | None]] = []
    client.find_path(on_attempt=lambda url, failure: seen.append((url, failure)))
    assert [url for url, _ in seen] == [
        "http://printer.example:631/ipp/print",
        "http://printer.example:631/ipp/printer",
        "http://printer.example:631/ipp/port1",
    ]
    assert [failure is None for _, failure in seen] == [False, False, True]


def test_find_path_restores_the_original_path_on_failure():
    client = StubClient(None)
    with pytest.raises(IppError, match="no IPP endpoint answered"):
        client.find_path()
    assert client.path == COMMON_PATHS[0]


def test_client_normalises_a_path_without_a_leading_slash():
    assert IppClient("printer.example", path="ipp/port1").printer_uri.endswith("/ipp/port1")
