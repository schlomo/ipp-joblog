"""Minimal IPP/2.0 codec: enough to ask a printer for its job history.

RFC 8010 encoding, RFC 8011 semantics. Only the operations we need are
implemented, so the whole thing stays small enough to test byte for byte.
"""

from __future__ import annotations

import socket
import ssl
import struct
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import IntEnum
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

IPP_VERSION = (2, 0)
IPP_CONTENT_TYPE = "application/ipp"

# The IPP resource path is not standardised. These cover AirPrint/IPP Everywhere
# printers, CUPS queues and older HP Jetdirect firmware.
COMMON_PATHS = ("/ipp/print", "/ipp/printer", "/ipp/port1", "/", "/printers/print")

# 631 is IPP's own port. Some printers answer IPP on their web ports instead,
# and a few only over TLS, so those are worth a look before giving up.
COMMON_PORTS = (631, 80, 443)
PORT_PROBE_TIMEOUT = 3.0


# What each scheme implies when a URL does not spell out a port.
SCHEME_PORTS = {"ipp": 631, "ipps": 443, "http": 80, "https": 443}


@dataclass(frozen=True, slots=True)
class Target:
    """Where to find a printer: a host, and optionally how to reach it.

    ``secure`` is ``None`` when the address did not say, leaving the port to
    decide. Stated outright it wins: a printer offering IPPS on 631 is legal
    and cannot be described by a port number alone.
    """

    host: str
    port: int | None = None
    path: str | None = None
    secure: bool | None = None

    @property
    def pinned(self) -> bool:
        """Nothing is left to discover."""
        return bool(self.port and self.path)


def parse_target(value: str) -> Target:
    """Read a printer address, from a bare name up to a full URL.

    ``hpm880``, ``192.168.1.50:80`` and ``ipp://hpm880:631/ipp/print`` are all
    accepted, so the device URI that ``lpstat -v`` prints for a CUPS queue can
    be pasted in unchanged.
    """
    text = value.strip()
    if "://" in text:
        parts = urlsplit(text)
        scheme = parts.scheme.lower()
        return Target(
            host=parts.hostname or "",
            port=parts.port or SCHEME_PORTS.get(scheme),
            path=parts.path if parts.path not in ("", "/") else None,
            secure=scheme in ("ipps", "https") if scheme in SCHEME_PORTS else None,
        )
    host, separator, port = text.rpartition(":")
    if separator and port.isdigit() and ":" not in host:  # not an IPv6 literal
        return Target(host=host, port=int(port))
    return Target(host=text)


# Printers present self-signed certificates, universally, so verifying would
# reject every one of them. This tool only reads, sends no credentials, and
# trusts nothing it gets back beyond parsing it, so an unverified channel gives
# up nothing it had. TLS is still worth speaking: some printers offer only it.
_TLS = ssl.create_default_context()
_TLS.check_hostname = False
_TLS.verify_mode = ssl.CERT_NONE


def port_state(host: str, port: int, timeout: float = PORT_PROBE_TIMEOUT) -> str:
    """Whether anything is listening, before spending a request finding out.

    Worth distinguishing: a refused port means the host is there and has
    nothing on it, which is a different problem from a host that never answers.
    """
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return "open"
    except ConnectionRefusedError:
        return "refused"
    except socket.gaierror:
        return "unknown host"
    except OSError:
        return "no answer"


def _endpoint_order(ports: tuple[int, ...]) -> list[tuple[int, bool]]:
    """Ports paired with how to speak to them, likeliest first.

    Plain HTTP on every port before TLS on any, since that is what nearly all
    printers want -- but TLS afterwards, because a printer offering only IPPS
    is otherwise invisible.
    """
    return [(port, port == 443) for port in ports] + [(port, True) for port in ports if port != 443]


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


def _take(data: bytes, offset: int, count: int) -> bytes:
    """Read ``count`` octets, or say the response is not IPP.

    Anything can answer an HTTP request with 200 and a body: a FRITZ!Box serves
    its own web page on ``/``. Decoding that as IPP runs off the end, and a
    struct error thrown from here is neither an IppError nor an OSError, so it
    escapes every handler and kills the run instead of moving to the next
    candidate endpoint.
    """
    chunk = data[offset : offset + count]
    if len(chunk) < count:
        raise IppError("not an IPP response: it ends mid-attribute")
    return chunk


def decode_response(data: bytes) -> tuple[int, list[dict[str, Attribute]]]:
    """Return ``(status_code, groups)`` where each group is name -> Attribute."""
    if len(data) < 8:
        raise IppError("truncated IPP response")
    status_code = struct.unpack(">H", data[2:4])[0]

    groups: list[dict[str, Attribute]] = []
    current: dict[str, Attribute] | None = None
    previous: Attribute | None = None
    offset = 8
    ended = False

    while offset < len(data):
        tag = data[offset]
        offset += 1
        if tag in DELIMITERS:
            if tag == Tag.END_OF_ATTRIBUTES:
                ended = True
                break
            current = {}
            groups.append(current)
            previous = None
            continue

        name_length = struct.unpack(">H", _take(data, offset, 2))[0]
        offset += 2
        name = _take(data, offset, name_length).decode("utf-8", "replace")
        offset += name_length
        value_length = struct.unpack(">H", _take(data, offset, 2))[0]
        offset += 2
        raw = _take(data, offset, value_length)
        offset += value_length

        if current is None:  # values before any delimiter: malformed, ignore
            continue
        value = _decode_value(tag, raw)
        if name_length == 0 and previous is not None:  # additional value
            previous.values.append(value)
            continue
        previous = Attribute(tag, name, [value])
        current[name] = previous

    if not ended:
        # Running out before the end-of-attributes tag means the body was cut
        # short, or was never IPP to begin with. Either way it is not an answer.
        raise IppError("not an IPP response: it ends before the attributes do")
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
        secure: bool | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.path = _normalise_path(path)
        #: None means "decide from the port"; stated outright it wins.
        self.secure = secure
        self._timeout = timeout
        self._request_id = 0

    @property
    def printer_uri(self) -> str:
        return f"{'ipps' if self.over_tls else 'ipp'}://{self.host}:{self.port}{self.path}"

    @property
    def over_tls(self) -> bool:
        return self.port == 443 if self.secure is None else self.secure

    @property
    def scheme(self) -> str:
        return "https" if self.over_tls else "http"

    @property
    def _http_url(self) -> str:
        return f"{self.scheme}://{self.host}:{self.port}{self.path}"

    def _request(
        self, operation: Operation, attributes: list[tuple[int, str, list[Any]]]
    ) -> list[dict[str, Attribute]]:
        self._request_id += 1
        body = encode_request(operation, self._request_id, attributes)
        request = Request(self._http_url, data=body, headers={"Content-Type": IPP_CONTENT_TYPE})
        try:
            with urlopen(request, timeout=self._timeout, context=_TLS) as response:
                # Plenty of things answer 200 with a body that is not IPP at all.
                content_type = response.headers.get_content_type()
                if content_type != IPP_CONTENT_TYPE:
                    raise IppError(
                        f"{operation.name}: {self._http_url} answered with "
                        f"{content_type or 'no content type'}, not {IPP_CONTENT_TYPE}"
                    )
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

    def find_endpoint(
        self,
        paths: tuple[str, ...] = COMMON_PATHS,
        ports: tuple[int, ...] = COMMON_PORTS,
        on_attempt: Callable[[str, str | None], None] | None = None,
    ) -> tuple[int, str]:
        """Find where this printer speaks IPP, as ``(port, path)``.

        Both halves are vendor-specific. Each port is checked for a listener
        first, so a closed one costs a connection rather than a request per
        path, and the caller learns that nothing was listening at all -- which
        is the difference between "wrong path" and "not an IPP printer".
        """
        original = (self.port, self.path, self.secure)
        try:
            for port, secure in _endpoint_order(ports):
                self.port, self.secure = port, secure
                state = port_state(self.host, port, min(self._timeout, PORT_PROBE_TIMEOUT))
                if state != "open":
                    if on_attempt:
                        on_attempt(f"{self.scheme}://{self.host}:{port}", state)
                    continue
                for candidate in paths:
                    self.path = candidate
                    try:
                        self.printer_attributes()
                    except (IppError, OSError) as error:
                        if on_attempt:
                            on_attempt(self._http_url, str(error))
                        continue
                    if on_attempt:
                        on_attempt(self._http_url, None)
                    return port, candidate
        finally:
            self.port, self.path, self.secure = original
        raise IppError(
            f"no IPP endpoint answered on {self.host}; tried ports "
            f"{', '.join(str(port) for port in ports)} and paths {', '.join(paths)}"
        )
