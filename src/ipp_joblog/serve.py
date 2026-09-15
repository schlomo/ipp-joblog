"""A poll loop and a static web server in one process.

Pages are written to a directory after every poll and the server hands that
directory out, so nothing is generated per request and the server stays a plain
stdlib file server with no application logic in it.

One file per printer, named after its database -- ``hpm880.sqlite3`` is served
as ``hpm880.html`` -- plus an ``index.html`` listing every printer the state
directory knows about.
"""

from __future__ import annotations

import os
import tempfile
import threading
from datetime import datetime
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ipp_joblog.page import Dashboard, Overview
from ipp_joblog.store import JobStore, PrinterSummary, databases


class QuietHandler(SimpleHTTPRequestHandler):
    """Serves one directory without logging every request."""

    def log_message(self, *_: object) -> None:
        return


def _publish(directory: Path, name: str, html: str) -> Path:
    """Write a page atomically, so a reader never sees a half-written file.

    Every writer gets its own temporary file. One ``serve`` per printer is the
    expected deployment and they all rewrite ``index.html``, so a shared
    temporary name would let two of them interleave inside it and publish the
    mixture -- or have one rename the other's file out from under it. With a
    file each, the rename is the only thing they contend for, and the loser
    simply published a page that the winner then replaced.
    """
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / name
    handle, temporary = tempfile.mkstemp(dir=directory, prefix=f".{name}.", suffix=".tmp")
    pending = Path(temporary)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(html)
        pending.chmod(0o644)  # mkstemp is private by default; these pages are served
        pending.replace(target)
    finally:
        pending.unlink(missing_ok=True)
    return target


def write_page(store: JobStore, directory: Path, *, printer: str, refresh_seconds: int) -> Path:
    """Render one printer's dashboard, named after its database."""
    dashboard = Dashboard(
        printer=printer,
        generated_at=datetime.now().astimezone(),
        totals=store.totals_by_user(),
        recent_jobs=store.recent(),
        refresh_seconds=refresh_seconds,
        facts=store.facts(),
    )
    return _publish(directory, f"{store.summarise().slug}.html", dashboard.render())


def summarise_all(state_dir: Path) -> list[PrinterSummary]:
    """Read every printer database in the state directory, busiest first."""
    summaries = []
    for path in databases(state_dir):
        with JobStore(path) as store:
            summaries.append(store.summarise())
    return sorted(summaries, key=lambda printer: (-printer.pages, printer.slug))


def write_index(state_dir: Path, directory: Path, *, refresh_seconds: int) -> Path:
    """Render the overview of every printer, so one page leads to them all."""
    overview = Overview(
        printers=summarise_all(state_dir),
        generated_at=datetime.now().astimezone(),
        refresh_seconds=refresh_seconds,
    )
    return _publish(directory, "index.html", overview.render())


def start_server(directory: Path, port: int, host: str = "") -> ThreadingHTTPServer:
    """Serve ``directory`` on ``port`` from a daemon thread."""
    directory.mkdir(parents=True, exist_ok=True)
    handler = partial(QuietHandler, directory=str(directory))
    server = ThreadingHTTPServer((host, port), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
