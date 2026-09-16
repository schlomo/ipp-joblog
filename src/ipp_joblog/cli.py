"""Command line entry point: parse arguments, run a command, print the result."""

from __future__ import annotations

import argparse
import os
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path

from ipp_joblog import diagnose as diagnostics
from ipp_joblog.ipp import (
    COMMON_PATHS,
    Attempt,
    IppClient,
    IppError,
    Target,
    parse_target,
)
from ipp_joblog.jobs import PrinterJobLog
from ipp_joblog.output import job_line, jobs_csv, totals_csv, totals_json, totals_table
from ipp_joblog.poller import Poller, PollResult
from ipp_joblog.probe import collect_facts, probe
from ipp_joblog.serve import start_server, write_index, write_page
from ipp_joblog.store import DatabaseChoiceError, JobStore, resolve_database
from ipp_joblog.version import resolve as resolve_version

DEFAULT_STATE_DIR = Path(os.environ.get("IPP_JOBLOG_STATE_DIR", "."))
DEFAULT_HTML_DIR = os.environ.get("IPP_JOBLOG_HTML_DIR")
DEFAULT_PORT = int(os.environ.get("IPP_JOBLOG_PORT", "8080"))
# Short, because printers that keep only the last job or two are common; the
# cost of polling often is a few small HTTP requests, the cost of polling too
# slowly is jobs that are gone before they are ever seen.
DEFAULT_INTERVAL = 15.0
# meta refresh of 0 means "reload now", which would spin the browser.
MINIMUM_REFRESH = 1

ROLLOVER_WARNING = (
    "warning: printer history rolled over between polls; some jobs were missed. Poll more often."
)


@dataclass(frozen=True, slots=True)
class Settings:
    """Everything a command needs, lifted out of the argparse namespace.

    Commands take this rather than a bare ``Namespace`` so what they depend on
    is visible and typed.
    """

    host: str | None
    state_dir: Path
    timeout: float
    path: str | None
    interval: float = DEFAULT_INTERVAL
    html_dir: Path | None = None
    port: int = DEFAULT_PORT
    bind: str = ""
    watch_seconds: float = 0.0
    since: datetime | None = None
    until: datetime | None = None
    output_format: str = "text"

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> Settings:
        return cls(
            host=args.host,
            state_dir=args.state_dir,
            timeout=args.timeout,
            path=args.path,
            interval=getattr(args, "interval", DEFAULT_INTERVAL),
            html_dir=getattr(args, "html_dir", None),
            port=getattr(args, "port", DEFAULT_PORT),
            bind=getattr(args, "bind", ""),
            watch_seconds=getattr(args, "watch", 0.0) or 0.0,
            since=getattr(args, "since", None),
            until=getattr(args, "until", None),
            output_format=getattr(args, "format", "text"),
        )

    @property
    def target(self) -> Target:
        """The printer address, with --path still able to override the path."""
        found = parse_target(self.host or "")
        return replace(found, path=self.path or found.path)

    @property
    def database(self) -> Path:
        """This printer's database, or the only one present when no host is given."""
        # The host alone, so ipp://hpm880/ipp/print and hpm880 are one printer.
        return resolve_database(self.state_dir, self.target.host or None)

    @property
    def page_dir(self) -> Path:
        """Where the dashboard is written; inside the state directory by default."""
        return Path(self.html_dir) if self.html_dir else self.state_dir / "public"

    @property
    def refresh_seconds(self) -> int:
        """How long the dashboard waits before reloading itself.

        The same as the poll interval, so a reader sees a page at most one cycle
        out of date.
        """
        return max(round(self.interval), MINIMUM_REFRESH)


def notice(message: str) -> None:
    """Operational messages go to stderr, so stdout stays pipeable data."""
    print(message, file=sys.stderr, flush=True)


@contextmanager
def open_store(settings: Settings) -> Iterator[JobStore]:
    """Open the history database and say what opening it did."""
    path = settings.database
    with JobStore(path) as store:
        if store.created:
            notice(f"created {path} at schema v{store.version}")
        elif store.applied_migrations:
            applied = ", ".join(f"v{number}" for number in store.applied_migrations)
            notice(f"upgraded {path} to schema v{store.version} (applied {applied})")
        yield store


def parse_moment(value: str) -> datetime:
    """Accept an ISO timestamp or a plain age such as ``30d`` or ``12h``."""
    units = {"d": "days", "h": "hours", "m": "minutes"}
    if value and value[-1] in units and value[:-1].isdigit():
        return datetime.now().astimezone() - timedelta(**{units[value[-1]]: int(value[:-1])})
    moment = datetime.fromisoformat(value)
    return moment if moment.tzinfo else moment.astimezone()


def report_poll(poller: Poller) -> PollResult:
    """Run one poll and print what changed."""
    result = poller.poll()
    if result.rolled_over:
        notice(ROLLOVER_WARNING)
    for job in result.stored:
        notice(job_line(job))
    return result


def warn_about_clock(skew: timedelta) -> None:
    """Say what was measured, and name the likeliest cause without assuming it.

    A Brother MFC-L3770CDW showed the right time on its own front page while
    reporting job times an hour ahead: its firmware applies daylight saving to
    the display but not to the offset it sends over IPP. So "your clock is
    wrong" is often not the problem, and saying so sends people to a settings
    page that already looks correct.
    """
    minutes = round(skew.total_seconds() / 60)
    notice(
        f"warning: this printer reports job times {minutes} minutes ahead of this machine, "
        "so stored timestamps are off by that much."
    )
    notice(
        "  if the printer's own clock looks right, its firmware may apply daylight saving "
        "to the display but not to the UTC offset it reports. Setting the printer's time "
        "zone to the current total offset with automatic daylight saving off works around it."
    )


def poll_forever(poller: Poller, interval: float, after_poll: Callable[[], None] | None) -> int:
    """Poll until interrupted.

    A printer that is asleep, rebooting or unplugged must not end the loop, so
    poll failures are reported and retried on the next tick. ``after_poll`` runs
    every cycle, successful or not, so the dashboard keeps its clock moving.
    """
    warned_about_clock = False
    while True:
        try:
            result = report_poll(poller)
            skew = result.clock_skew
            if skew and not warned_about_clock:
                warn_about_clock(skew)  # once per run: the cause will not change
                warned_about_clock = True
        except (IppError, OSError) as error:
            notice(f"poll failed: {error}")
        if after_poll:
            after_poll()
        try:
            time.sleep(interval)
        except KeyboardInterrupt:
            return 0


def page_writer(settings: Settings, store: JobStore) -> Callable[[], None]:
    """A no-argument callable that refreshes this printer's page and the index."""

    def write() -> None:
        write_page(
            store,
            settings.page_dir,
            printer=settings.host or "printer",
            refresh_seconds=settings.refresh_seconds,
        )
        write_index(settings.state_dir, settings.page_dir, refresh_seconds=settings.refresh_seconds)

    return write


def learn_about_printer(client: IppClient, settings: Settings, store: JobStore) -> None:
    """Record what the printer says about itself, once, at startup.

    A printer's identity does not change while we watch it, so this is read once
    rather than every poll, and the dashboard reads it back from the database.
    """
    try:
        store.remember_facts(collect_facts(client, settings.host or ""))
    except (IppError, OSError) as error:
        notice(f"could not read printer details: {error}")


def connect(settings: Settings, *, quiet: bool = False) -> IppClient:
    """An IPP client aimed at wherever this printer actually listens.

    A port scan comes first: every candidate path is worth trying only on a
    port that has something behind it, and knowing which ports are open is what
    makes a failure explicable rather than just a failure.
    """
    target = settings.target
    client = IppClient(
        target.host,
        timeout=settings.timeout,
        port=target.port or 631,
        path=target.path,
        secure=target.secure,
    )
    if target.pinned:
        if not quiet:
            notice(f"using IPP endpoint (given): {client.printer_uri}")
        return client

    states = diagnostics.scan(target.host, min(settings.timeout, 3.0))
    connect.last_scan = states  # the caller reports it; discovery just uses it
    if not quiet:
        notice(f"scanning {target.host}")
        for line in diagnostics.port_table(states):
            notice(line)

    ports = (target.port,) if target.port else diagnostics.worth_trying(states)
    paths = (target.path,) if target.path else COMMON_PATHS
    attempts: list[Attempt] = []
    try:
        # The client is left pointing at what was found, TLS flag included.
        client.find_endpoint(paths=paths, ports=ports, on_attempt=attempts.append)
    finally:
        connect.last_attempts = attempts
        if not quiet and attempts:
            notice("looking for IPP on the open ports")
            for line in summarise_attempts(attempts):
                notice(line)
    if not quiet:
        notice(f"found IPP at {client.printer_uri}")
    return client


def summarise_attempts(attempts: list[Attempt]) -> list[str]:
    """One line per port, rather than one per path.

    Five paths against two ports is twenty near-identical failures, and the
    interesting part is only ever which reason came back.
    """
    by_port: dict[tuple[int, bool], list[Attempt]] = {}
    for attempt in attempts:
        if attempt.path is not None:  # a closed port is already in the scan table
            by_port.setdefault((attempt.port, attempt.secure), []).append(attempt)

    lines = []
    for (port, secure), tried in by_port.items():
        label = f"{port}{' (TLS)' if secure else ''}"
        found = next((one for one in tried if one.failure is None), None)
        if found:
            lines.append(f"  {label:<11} answered IPP at {found.path}")
            continue
        reasons: dict[str, int] = {}
        for one in tried:
            reasons[one.failure or ""] = reasons.get(one.failure or "", 0) + 1
        summary = ", ".join(
            f"{reason} x{count}" if count > 1 else reason for reason, count in reasons.items()
        )
        lines.append(f"  {label:<11} {len(tried)} paths tried: {summary}")
    return lines


def configuration_advice(client: IppClient) -> list[str]:
    """How to keep using an endpoint that was not the obvious one."""
    if (client.port, client.path) == (631, COMMON_PATHS[0]):
        return []
    url = client.printer_uri
    return [
        "",
        f"This printer answers on port {client.port} at {client.path}, not where printers",
        "usually do. Discovery finds it every time, so nothing has to be configured — but",
        "giving the full address skips the scan and makes startup immediate:",
        f"  ipp-joblog --host {url} serve",
        f"  or IPP_PRINTER_HOST={url}",
    ]


def reach(settings: Settings) -> IppClient:
    """Connect for a long-running command, which must survive a sleeping printer.

    Discovery failing at startup is not fatal here: the poll loop already
    retries, so fall back to the usual endpoint and let it try again later.
    """
    try:
        return connect(settings)
    except (IppError, OSError) as error:
        notice(f"could not find an IPP endpoint yet ({error}); will keep trying")
        target = settings.target
        return IppClient(
            target.host,
            timeout=settings.timeout,
            port=target.port or 631,
            path=target.path,
            secure=target.secure,
        )


def command_probe(settings: Settings) -> int:
    """Report whether this printer can actually be accounted for.

    A printer that cannot be is worth more than an error, so the diagnostic
    dump runs by itself: the report to paste into an issue is the output of the
    command that just failed.
    """
    try:
        client = connect(settings)
    except (IppError, OSError):
        host = settings.target.host
        states = connect.last_scan
        print("")
        print("\n".join(diagnostics.verdict(host, states)))
        # Nothing to report when the scan already explained itself, and nothing
        # to watch when there is no IPP endpoint to watch it on.
        if not diagnostics.explained(states):
            print("\n".join(diagnostics.issue_invitation(host, can_watch=False)))
        return 1

    report = probe(client)
    print("\n".join(report.lines()))
    if report.usable:
        print("\nThis printer can be accounted for.")
        print("\n".join(configuration_advice(client)))
        return 0

    print("")
    print(report.problem().strip())
    print("\nFull detail, since this printer did not give us what we need:\n")
    print("\n".join(diagnostics.report(client)))
    print("\n".join(diagnostics.issue_invitation(settings.host)))
    return 1


def command_diagnose(settings: Settings) -> int:
    """Dump everything this printer will say, for a bug report."""
    client = connect(settings)
    print("\n".join(configuration_advice(client)))
    if settings.watch_seconds:
        print("\n".join(diagnostics.watch(client, settings.watch_seconds)))
    else:
        print("\n".join(diagnostics.report(client)))
    print("\n".join(diagnostics.issue_invitation(settings.host)))
    return 0


def command_poll(settings: Settings) -> int:
    client = connect(settings, quiet=True)
    with open_store(settings) as store:
        learn_about_printer(client, settings, store)
        skew = report_poll(Poller(PrinterJobLog.using(client), store)).clock_skew
        if skew:
            warn_about_clock(skew)
    return 0


def command_watch(settings: Settings) -> int:
    client = reach(settings)
    with open_store(settings) as store:
        learn_about_printer(client, settings, store)
        poller = Poller(PrinterJobLog.using(client), store)
        refresh = page_writer(settings, store) if settings.html_dir else None
        return poll_forever(poller, settings.interval, refresh)


def command_serve(settings: Settings) -> int:
    """Poll on an interval and serve the generated page over HTTP."""
    directory = settings.page_dir
    client = reach(settings)
    with open_store(settings) as store:
        learn_about_printer(client, settings, store)
        poller = Poller(PrinterJobLog.using(client), store)
        refresh = page_writer(settings, store)
        refresh()  # so the first request never races the first poll
        server = start_server(directory, settings.port, settings.bind)
        page = f"{settings.database.stem}.html"
        notice(f"serving http://{settings.bind or '0.0.0.0'}:{settings.port}/ from {directory}")
        notice(f"  index.html lists every printer; this one is {page}")
        try:
            return poll_forever(poller, settings.interval, refresh)
        finally:
            server.shutdown()


REPORT_FORMATS = {"text": totals_table, "csv": totals_csv, "json": totals_json}


def command_report(settings: Settings) -> int:
    with open_store(settings) as store:
        totals = store.totals_by_user(since=settings.since, until=settings.until)
    print(REPORT_FORMATS[settings.output_format](totals).rstrip("\n"))
    return 0


def command_jobs(settings: Settings) -> int:
    with open_store(settings) as store:
        rows = store.rows(since=settings.since, until=settings.until)
    print(jobs_csv(rows).rstrip("\n"))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ipp-joblog",
        description="Collect per-user page counts from an AirPrint/IPP printer.",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("IPP_PRINTER_HOST"),
        help="printer address (env: IPP_PRINTER_HOST): a name or IP, optionally with a "
        "port or as a full URL such as ipp://printer:631/ipp/print — the device URI that "
        "`lpstat -v` prints for a CUPS queue works as-is",
    )
    parser.add_argument(
        "--state-dir",
        type=Path,
        default=DEFAULT_STATE_DIR,
        help="directory holding one database per printer "
        f"(env: IPP_JOBLOG_STATE_DIR, default: {DEFAULT_STATE_DIR})",
    )
    parser.add_argument(
        "--path",
        default=os.environ.get("IPP_PATH"),
        help=f"IPP resource path (env: IPP_PATH, default: {COMMON_PATHS[0]}); "
        "'probe' discovers it when unset",
    )
    parser.add_argument("--timeout", type=float, default=20.0, help="IPP timeout in seconds")
    parser.add_argument("--version", action="version", version=f"%(prog)s {resolve_version()}")

    subparsers = parser.add_subparsers(dest="command", required=True)

    def add(name: str, handler: object, help_text: str, *, needs_host: bool = True):
        sub = subparsers.add_parser(name, help=help_text)
        sub.set_defaults(handler=handler, needs_host=needs_host)
        return sub

    def add_interval(sub: argparse.ArgumentParser) -> None:
        sub.add_argument(
            "--interval",
            type=float,
            default=DEFAULT_INTERVAL,
            help=f"seconds between polls (default: {DEFAULT_INTERVAL:g})",
        )

    def add_window(sub: argparse.ArgumentParser) -> argparse.ArgumentParser:
        sub.add_argument("--since", type=parse_moment, help="ISO timestamp or age like 30d")
        sub.add_argument("--until", type=parse_moment, help="ISO timestamp or age like 1d")
        return sub

    add("probe", command_probe, "check whether a printer can be accounted for")

    diagnose = add("diagnose", command_diagnose, "dump everything a printer says, for a bug report")
    diagnose.add_argument(
        "--watch",
        type=float,
        metavar="SECONDS",
        help="instead, poll hard for this long and show any job seen while it prints "
        "(for printers that retain no finished jobs)",
    )
    add("poll", command_poll, "fetch once and store new jobs")

    watch = add("watch", command_watch, "poll on an interval until interrupted")
    add_interval(watch)
    watch.add_argument(
        "--html-dir", type=Path, default=None, help="also write index.html here after every poll"
    )

    serve = add("serve", command_serve, "poll on an interval and serve the page over HTTP")
    add_interval(serve)
    serve.add_argument(
        "--html-dir",
        type=Path,
        default=DEFAULT_HTML_DIR,
        help="where index.html is written (env: IPP_JOBLOG_HTML_DIR, default: <state-dir>/public)",
    )
    serve.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"HTTP port (env: IPP_JOBLOG_PORT, default: {DEFAULT_PORT})",
    )
    serve.add_argument(
        "--bind",
        default=os.environ.get("IPP_JOBLOG_BIND", ""),
        help="address to bind (default: all interfaces)",
    )

    add_window(
        add("report", command_report, "per-user page totals", needs_host=False)
    ).add_argument("--format", choices=("text", "csv", "json"), default="text")
    add_window(add("jobs", command_jobs, "stored jobs as CSV", needs_host=False))

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.needs_host and not args.host:
        parser.error("no printer host given; pass --host or set IPP_PRINTER_HOST")

    try:
        return args.handler(Settings.from_args(args))
    except (IppError, OSError, DatabaseChoiceError) as error:
        notice(f"error: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
