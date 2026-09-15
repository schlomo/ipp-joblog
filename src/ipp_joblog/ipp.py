"""Minimal IPP/2.0 codec: enough to ask a printer for its job history.

RFC 8010 encoding, RFC 8011 semantics. Only the operations we need are
implemented, so the whole thing stays small enough to test byte for byte.
"""

from __future__ import annotations

import struct
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from enum import IntEnum
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

IPP_VERSION = (2, 0)
IPP_CONTENT_TYPE = "application/ipp"

# The IPP resource path is not standardised. These cover AirPrint/IPP Everywhere
# printers, CUPS queues and older HP Jetdirect firmware.
COMMON_PATHS = ("/ipp/print", "/ipp/printer", "/ipp/port1", "/", "/printers/print")


def _normalise_path(path: str | None) -> str:
    """``None`` means the usual AirPrint path; a bare path gains its slash."""
    if not path:
        return COMMON_PATHS[0]
    return path if path.startswith("/") else f"/{path}"


class Operation(IntEnum):
    GET_JOBS = 0x000A
    GET_PRINTER_ATTRIBUTES = 0x000B


class Tag(IntEnum):
    OPERATION_ATTRIBUTES = 0x01
    JOB_ATTRIBUTES = 0x02
    END_OF_ATTRIBUTES = 0x03
    PRINTER_ATTRIBUTES = 0x04
    UNSUPPORTED_ATTRIBUTES = 0x05

    UNSUPPORTED_VALUE = 0x10
    UNKNOWN = 0x12
    NO_VALUE = 0x13
    INTEGER = 0x21
    BOOLEAN = 0x22
    ENUM = 0x23
    OCTET_STRING = 0x30
    DATE_TIME = 0x31
    RESOLUTION = 0x32
    RANGE_OF_INTEGER = 0x33
    BEG_COLLECTION = 0x34
    TEXT_WITH_LANGUAGE = 0x35
    NAME_WITH_LANGUAGE = 0x36
    END_COLLECTION = 0x37
    TEXT_WITHOUT_LANGUAGE = 0x41
    NAME_WITHOUT_LANGUAGE = 0x42
    KEYWORD = 0x44
    URI = 0x45
    URI_SCHEME = 0x46
    CHARSET = 0x47
    NATURAL_LANGUAGE = 0x48
    MIME_MEDIA_TYPE = 0x49
    MEMBER_ATTR_NAME = 0x4A


DELIMITERS = frozenset(
    {
        Tag.OPERATION_ATTRIBUTES,
        Tag.JOB_ATTRIBUTES,
        Tag.END_OF_ATTRIBUTES,
        Tag.PRINTER_ATTRIBUTES,
        Tag.UNSUPPORTED_ATTRIBUTES,
    }
)

_OUT_OF_BAND = frozenset({Tag.UNSUPPORTED_VALUE, Tag.UNKNOWN, Tag.NO_VALUE})


class IppError(RuntimeError):
    """The printer refused the request or sent something we cannot decode."""


class Attribute:
    """An IPP attribute: a name plus one or more values of the same tag."""

    __slots__ = ("name", "tag", "values")

    def __init__(self, tag: int, name: str, values: list[Any]) -> None:
        self.tag = tag
        self.name = name
        self.values = values

    @property
    def value(self) -> Any:
        return self.values[0] if self.values else None

    def __repr__(self) -> str:
        return f"Attribute({self.name!r}, {self.values!r})"


def _encode_text(value: str) -> bytes:
    raw = value.encode("utf-8")
    return struct.pack(">H", len(raw)) + raw


def _encode_attribute(tag: int, name: str, values: list[Any]) -> bytes:
    out = b""
    for index, value in enumerate(values):
        payload = struct.pack(">i", value) if tag == Tag.INTEGER else str(value).encode("utf-8")
        out += struct.pack(">B", tag)
        out += _encode_text(name if index == 0 else "")
        out += struct.pack(">H", len(payload)) + payload
    return out


def encode_request(
    operation: Operation, request_id: int, operation_attributes: list[tuple[int, str, list[Any]]]
) -> bytes:
    body = struct.pack(">BBHI", *IPP_VERSION, operation, request_id)
    body += struct.pack(">B", Tag.OPERATION_ATTRIBUTES)
    for tag, name, values in operation_attributes:
        body += _encode_attribute(tag, name, values)
    return body + struct.pack(">B", Tag.END_OF_ATTRIBUTES)


def _decode_datetime(raw: bytes) -> datetime | None:
    """Decode an RFC 2579 DateAndTime (11 octets)."""
    if len(raw) < 11:
        return None
    year, month, day, hour, minute, second, deci = struct.unpack(">HBBBBBB", raw[:8])
    direction = raw[8:9].decode("ascii", "replace")
    offset = timedelta(hours=raw[9], minutes=raw[10])
    if direction == "-":
        offset = -offset
    try:
        return datetime(year, month, day, hour, minute, second, deci * 100_000, timezone(offset))
    except ValueError:
        return None


def _decode_value(tag: int, raw: bytes) -> Any:
    if tag in _OUT_OF_BAND:
        return None
    if tag in (Tag.INTEGER, Tag.ENUM):
        return struct.unpack(">i", raw)[0] if len(raw) == 4 else None
    if tag == Tag.BOOLEAN:
        return bool(raw[0]) if raw else None
    if tag == Tag.DATE_TIME:
        return _decode_datetime(raw)
    if tag in (Tag.TEXT_WITH_LANGUAGE, Tag.NAME_WITH_LANGUAGE):
        language_length = struct.unpack(">H", raw[:2])[0]
        text_start = 2 + language_length + 2
        return raw[text_start:].decode("utf-8", "replace")
    if tag in (Tag.BEG_COLLECTION, Tag.END_COLLECTION, Tag.MEMBER_ATTR_NAME):
        return raw.decode("utf-8", "replace")
    return raw.decode("utf-8", "replace")


def decode_response(data: bytes) -> tuple[int, list[dict[str, Attribute]]]:
    """Return ``(status_code, groups)`` where each group is name -> Attribute."""
    if len(data) < 8:
        raise IppError("truncated IPP response")
    status_code = struct.unpack(">H", data[2:4])[0]

    groups: list[dict[str, Attribute]] = []
    current: dict[str, Attribute] | None = None
    previous: Attribute | None = None
    offset = 8

    while offset < len(data):
        tag = data[offset]
        offset += 1
        if tag in DELIMITERS:
            if tag == Tag.END_OF_ATTRIBUTES:
                break
            current = {}
            groups.append(current)
            previous = None
            continue

        name_length = struct.unpack(">H", data[offset : offset + 2])[0]
        offset += 2
        name = data[offset : offset + name_length].decode("utf-8", "replace")
        offset += name_length
        value_length = struct.unpack(">H", data[offset : offset + 2])[0]
        offset += 2
        raw = data[offset : offset + value_length]
        offset += value_length

        if current is None:  # values before any delimiter: malformed, ignore
            continue
        value = _decode_value(tag, raw)
        if name_length == 0 and previous is not None:  # additional value
            previous.values.append(value)
            continue
        previous = Attribute(tag, name, [value])
        current[name] = previous

    return status_code, groups


class IppClient:
    """Talks IPP over HTTP. Read-only: it only issues Get-* operations."""

    def __init__(
        self,
        host: str,
        *,
        timeout: float = 20.0,
        port: int = 631,
        path: str | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.path = _normalise_path(path)
        self._timeout = timeout
        self._request_id = 0

    @property
    def printer_uri(self) -> str:
        return f"ipp://{self.host}:{self.port}{self.path}"

    @property
    def _http_url(self) -> str:
        return f"http://{self.host}:{self.port}{self.path}"

    def _request(
        self, operation: Operation, attributes: list[tuple[int, str, list[Any]]]
    ) -> list[dict[str, Attribute]]:
        self._request_id += 1
        body = encode_request(operation, self._request_id, attributes)
        request = Request(self._http_url, data=body, headers={"Content-Type": IPP_CONTENT_TYPE})
        try:
            with urlopen(request, timeout=self._timeout) as response:
                payload = response.read()
        except HTTPError as error:
            raise IppError(f"{operation.name}: HTTP {error.code} from {self._http_url}") from error
        status_code, groups = decode_response(payload)
        if status_code >= 0x0100:
            raise IppError(f"{operation.name} failed with IPP status 0x{status_code:04x}")
        return groups

    def _base_attributes(self) -> list[tuple[int, str, list[Any]]]:
        return [
            (Tag.CHARSET, "attributes-charset", ["utf-8"]),
            (Tag.NATURAL_LANGUAGE, "attributes-natural-language", ["en"]),
            (Tag.URI, "printer-uri", [self.printer_uri]),
        ]

    def get_jobs(
        self, *, which_jobs: str = "completed", limit: int = 200
    ) -> list[dict[str, Attribute]]:
        """Fetch job attribute groups. ``which-jobs`` is ``completed`` or ``not-completed``."""
        attributes = [
            *self._base_attributes(),
            (Tag.KEYWORD, "which-jobs", [which_jobs]),
            (Tag.INTEGER, "limit", [limit]),
            (Tag.KEYWORD, "requested-attributes", ["all"]),
        ]
        groups = self._request(Operation.GET_JOBS, attributes)
        return [group for group in groups[1:] if "job-id" in group]

    def printer_attributes(self) -> dict[str, Attribute]:
        groups = self._request(Operation.GET_PRINTER_ATTRIBUTES, self._base_attributes())
        return groups[-1] if len(groups) > 1 else {}

    def find_path(
        self,
        candidates: tuple[str, ...] = COMMON_PATHS,
        on_attempt: Callable[[str, str | None], None] | None = None,
    ) -> str:
        """Return the first resource path that answers Get-Printer-Attributes.

        The path is vendor-specific, so a printer that ignores ``/ipp/print``
        may still speak IPP somewhere else. ``on_attempt`` is called with the
        URL tried and the failure reason, or ``None`` once one works.
        """
        original = self.path
        try:
            for candidate in candidates:
                self.path = candidate
                try:
                    self.printer_attributes()
                except (IppError, OSError) as error:
                    if on_attempt:
                        on_attempt(self._http_url, str(error))
                    continue
                if on_attempt:
                    on_attempt(self._http_url, None)
                return candidate
        finally:
            self.path = original
        raise IppError(
            f"no IPP endpoint answered on {self.host}:{self.port}; tried {', '.join(candidates)}"
        )
