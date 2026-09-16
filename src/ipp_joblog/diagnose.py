"""Everything a printer will tell us, for a printer that will not tell us enough.

`probe` runs this by itself when a printer cannot be accounted for, so the
report to paste into an issue is the same command that failed, and nothing has
to be installed alongside the container to get it.
"""

from __future__ import annotations

import time
from datetime import datetime

from ipp_joblog.ipp import (
    COMMON_PATHS,
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


def reachability(host: str, timeout: float, states: dict[int, str] | None = None) -> list[str]:
    """What is listening, and what that says about the printer."""
    states = scan(host, timeout) if states is None else states
    lines = [f"host: {host}"]
    lines += [f"  {port:<5} {states[port]:<13} {what}" for port, what in DIAGNOSTIC_PORTS]

    ipp, raw, lpd = states[631], states[9100], states[515]
    web = "open" in (states[80], states[443])

    if all(state == "unknown host" for state in states.values()):
        lines.append("\nThe name does not resolve. Check the spelling, or use the IP address.")
    elif not any(state == "open" for state in states.values()):
        if all(state == "refused" for state in states.values()):
            lines.append(
                "\nSomething is at that address and refuses every printing port, so it is"
                "\nprobably not the printer. A printer on Wi-Fi and DHCP may have moved;"
                "\ncheck the address on the printer itself."
            )
        else:
            lines.append(
                "\nNothing answered at all. If the printer sleeps deeply it may need waking;"
                "\notherwise check this machine can reach it and that no firewall is between."
            )
    elif ipp != "open" and (raw == "open" or lpd == "open"):
        carries = "9100" if raw == "open" else "515"
        lines.append(
            f"\nThis printer takes print data on {carries} but does not answer IPP. That is"
            "\nalmost certainly how your queue reaches it, and it is the answer: raw"
            "\nprinting hands the printer bytes and tells it nothing, so there is no per-user"
            "\njob history on the device for anything to read. ipp-joblog cannot help with"
            "\nthis printer, and nor can any tool that asks the printer who printed."
        )
        if web:
            lines.append(
                "\nIts web interface is open, so it is worth a look: some printers ship with"
                "\nIPP or AirPrint switched off, and turning it on is all that is missing."
            )
    elif ipp != "open" and web:
        lines.append(
            "\nIPP is closed but the web interface is open. Some printers let IPP or AirPrint"
            "\nbe disabled there while ordinary printing keeps working; worth checking."
        )
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
        "-" * 72,
        "This printer cannot be accounted for as things stand. If you would like it",
        "to be, please open an issue and paste everything above:",
        f"  {ISSUES}",
        "",
        "It helps to say how you normally print to it -- `lpstat -v` names the device",
        "URI for each queue -- and, if it retains no finished jobs, whether",
        f"  ipp-joblog --host {host} diagnose --watch 90",
        "shows anything while you send a page to it.",
        f"generated {datetime.now().astimezone():%Y-%m-%d %H:%M %z} by ipp-joblog",
        "-" * 72,
    ]


def unreachable_report(host: str, timeout: float) -> list[str]:
    """What to show when no endpoint answered at all."""
    return [
        "",
        "Nothing answered, so there is nothing to ask. What is reachable:",
        "",
        *reachability(host, timeout),
        f"  paths tried on any open port: {', '.join(COMMON_PATHS)}",
    ]
