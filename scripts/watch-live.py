"""Poll a printer hard while a job runs, to see what it exposes in flight.

Printers that retain no job history may still show a job while it is printing.
Start this, send a print job, and see whether anything appears and whether it
carries page counts.

    uv run python scripts/watch-live.py <host> [seconds]
"""

from __future__ import annotations

import sys
import time

sys.path.insert(0, "src")

from ipp_joblog.ipp import IppClient, IppError

# Brother advertises the underscore spelling; RFC 8011 uses the hyphen.
WHICH_JOBS = ("not-completed", "not_completed", "completed")
WATCHED = (
    "job-id",
    "job-name",
    "job-originating-user-name",
    "job-state",
    "job-impressions",
    "job-impressions-completed",
    "job-media-sheets",
    "job-media-sheets-completed",
    "job-k-octets",
    "date-time-at-creation",
    "date-time-at-completed",
    "time-at-creation",
    "time-at-completed",
)


def summarise(group: dict) -> str:
    parts = []
    for name in WATCHED:
        attribute = group.get(name)
        if attribute is not None and attribute.value is not None:
            parts.append(f"{name}={attribute.value!r}")
    return "  ".join(parts) or "(no watched attributes)"


def main(host: str, seconds: float) -> int:
    client = IppClient(host, timeout=5)
    client.path = client.find_path()
    print(f"watching {client.printer_uri} for {seconds:g}s — send a print job now\n", flush=True)

    seen: set[str] = set()
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        for which in WHICH_JOBS:
            try:
                groups = client.get_jobs(which_jobs=which, limit=100)
            except (IppError, OSError):
                continue
            for group in groups:
                line = f"[{which}] {summarise(group)}"
                if line not in seen:
                    seen.add(line)
                    print(line, flush=True)
        time.sleep(0.4)

    if not seen:
        print("\nNothing appeared in any queue at any point.")
        return 1
    print(f"\n{len(seen)} distinct observation(s).")
    return 0


if __name__ == "__main__":
    if not 2 <= len(sys.argv) <= 3:
        print(f"usage: {sys.argv[0]} <printer-host> [seconds]")
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1], float(sys.argv[2]) if len(sys.argv) == 3 else 90.0))
