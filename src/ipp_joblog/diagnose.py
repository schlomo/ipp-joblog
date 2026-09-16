"""Everything a printer will tell us, for a printer that will not tell us enough.

`probe` runs this by itself when a printer cannot be accounted for, so the
report to paste into an issue is the same command that failed, and nothing has
to be installed alongside the container to get it.
"""

from __future__ import annotations

import time
from datetime import datetime

from ipp_joblog.ipp import (
    COMMON_PORTS,
    Attribute,
    IppClient,
    IppError,
    port_state,
)

ISSUES = "https://github.com/schlomo/ipp-joblog/issues/new"

# Brother advertises the underscore spelling; RFC 8011 uses the hyphen.
JOB_QUEUES = ("completed", "not-completed", "not_completed")


# Beyond the ports IPP might live on, the ones that say what a printer *is*.
# A printer that takes raw data but not IPP explains itself by being here.
DIAGNOSTIC_PORTS = (
    (631, "IPP, which is what this tool reads"),
    (80, "web interface, and IPP on some printers"),
    (443, "web interface over TLS, and IPPS on some printers"),
    (9100, "raw printing (socket://, JetDirect)"),
    (515, "LPD (lpd://)"),
)


def scan(host: str, timeout: float) -> dict[int, str]:
    """Which printing ports are listening."""
    return {port: port_state(host, port, timeout) for port, _ in DIAGNOSTIC_PORTS}


def worth_trying(states: dict[int, str]) -> tuple[int, ...]:
    """The ports worth an IPP request, in the order to try them.

    Only a refused port is proof that nothing is there. A port that did not
    answer the scan in time may still be a printer waking up -- a sleeping
    M880 answers 80 and 9100 from its network card while its IPP service is
    still coming to, and excluding 631 on that evidence breaks a printer that
    works perfectly a second later.
    """
    candidates = tuple(port for port in COMMON_PORTS if states.get(port) != "refused")
    return candidates or COMMON_PORTS


def port_table(states: dict[int, str]) -> list[str]:
    """The scan, printed once and referred to afterwards."""
    return [f"  {port:<5} {states[port]:<9} {what}" for port, what in DIAGNOSTIC_PORTS]


def verdict(host: str, states: dict[int, str]) -> list[str]:
    """What the pattern of open ports means, said once."""
    raw, lpd = states[9100], states[515]
    web = [port for port in (80, 443) if states[port] == "open"]

    if all(state == "unknown host" for state in states.values()):
        return [f"{host} does not resolve. Check the spelling, or use the IP address."]

    if not any(state == "open" for state in states.values()):
        if all(state == "refused" for state in states.values()):
            return [
                f"{host} refuses every printing port, so it is probably not the printer any",
                "more. One on Wi-Fi and DHCP may have moved; check the address on the device.",
            ]
        return [
            f"{host} did not answer at all. A deeply sleeping printer may need waking;",
            "otherwise check this machine can reach it and that nothing filters between.",
        ]

    lines = [f"{host} does not answer IPP."]
    if raw == "open" or lpd == "open":
        carries = "9100" if raw == "open" else "515"
        lines += [
            "",
            f"It takes print data on {carries} — raw printing, which hands the printer bytes",
            "and tells it nothing about who sent them. No per-user history exists on the",
            "device for any tool to read, so ipp-joblog cannot account for this printer.",
        ]
    if web:
        ports = " and ".join(str(port) for port in web)
        lines += [
            "",
            f"Its web interface is open ({ports}), so it is worth checking whether IPP or",
            "AirPrint is simply switched off there.",
        ]
    return lines


def _describe(attributes: dict[str, Attribute], names: tuple[str, ...]) -> list[str]:
    return [f"  {name} = {attributes[name].values}" for name in names if name in attributes]


INTERESTING = (
    "printer-make-and-model",
    "printer-state-reasons",
    "ipp-versions-supported",
    "which-jobs-supported",
    "printer-more-info",
    "document-format-supported",
)


def report(client: IppClient) -> list[str]:
    """A full dump of one printer, assuming its endpoint is already known."""
    lines = [f"endpoint: {client.printer_uri}"]
    attributes = client.printer_attributes()
    lines.append(f"printer reports {len(attributes)} attributes")
    lines += _describe(attributes, INTERESTING)

    operations = attributes.get("operations-supported")
    lines.append(f"operations-supported: {sorted(operations.values) if operations else 'none'}")

    for which in JOB_QUEUES:
        try:
            groups = client.get_jobs(which_jobs=which, limit=500)
        except (IppError, OSError) as error:
            lines.append(f"\nGet-Jobs which-jobs={which}: failed ({error})")
            continue
        lines.append(f"\nGet-Jobs which-jobs={which}: {len(groups)} job(s)")
        if groups:
            lines.append("  attributes on the newest:")
            for key, attribute in sorted(groups[-1].items()):
                value = attribute.values if len(attribute.values) > 1 else attribute.value
                lines.append(f"    {key} = {value!r}")
    return lines


def watch(client: IppClient, seconds: float) -> list[str]:
    """Poll hard while a job runs, for printers that retain no finished jobs.

    Some printers show a job only while it is printing. Nothing here is stored;
    it answers whether anything is visible at all, and whether it carries page
    counts.
    """
    watched = (
        "job-id",
        "job-name",
        "job-originating-user-name",
        "job-state",
        "job-impressions-completed",
        "job-media-sheets-completed",
        "date-time-at-completed",
        "time-at-creation",
    )
    lines = [f"watching {client.printer_uri} for {seconds:g}s -- send a print job now"]
    seen: set[str] = set()
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        for which in JOB_QUEUES:
            try:
                groups = client.get_jobs(which_jobs=which, limit=100)
            except (IppError, OSError):
                continue
            for group in groups:
                shown = "  ".join(
                    f"{name}={group[name].value!r}"
                    for name in watched
                    if name in group and group[name].value is not None
                )
                line = f"[{which}] {shown}"
                if line not in seen:
                    seen.add(line)
                    lines.append(line)
        time.sleep(0.4)
    if len(seen) == 0:
        lines.append("nothing appeared in any queue at any point.")
    return lines


def issue_invitation(host: str) -> list[str]:
    """Turn a dead end into a useful bug report."""
    return [
        "",
        f"To report this printer: {ISSUES}",
        "Please include everything above, how you print to it (`lpstat -v` names the",
        "device URI for each queue), and — if it keeps no finished jobs — the output of:",
        f"  ipp-joblog --host {host} diagnose --watch 90",
        f"  (ipp-joblog {datetime.now().astimezone():%Y-%m-%d %H:%M %z})",
    ]
