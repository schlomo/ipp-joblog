"""Dump what a printer will tell us over IPP, for diagnosing an unsupported one.

Run against any printer and paste the output into an issue:

    uv run python scripts/diagnose.py <host>
"""

from __future__ import annotations

import sys

sys.path.insert(0, "src")

from ipp_joblog.ipp import COMMON_PATHS, IppClient, IppError

INTERESTING = (
    "printer-make-and-model",
    "printer-state-reasons",
    "ipp-versions-supported",
    "which-jobs-supported",
    "job-history-interval-configured",
    "job-history-attributes-configured",
    "printer-settable-attributes-supported",
    "document-format-supported",
)


def main(host: str) -> int:
    client = IppClient(host, timeout=15)
    print(f"host: {host}")

    try:
        path = client.find_path(
            on_attempt=lambda url, bad: print(
                f"  {'ok  ' if bad is None else 'fail'} {url}"
                + ("" if bad is None else f"  ({bad})")
            )
        )
    except (IppError, OSError) as error:
        print(f"no IPP endpoint: {error}")
        return 1
    client.path = path
    print(f"endpoint: {client.printer_uri}\n")

    attributes = client.printer_attributes()
    print(f"printer reports {len(attributes)} attributes")
    for name in INTERESTING:
        if name in attributes:
            print(f"  {name} = {attributes[name].values}")

    print("\noperations-supported:", end=" ")
    operations = attributes.get("operations-supported")
    print(sorted(operations.values) if operations else "not reported")

    for which in ("completed", "not-completed", "not_completed"):
        try:
            groups = client.get_jobs(which_jobs=which, limit=500)
        except (IppError, OSError) as error:
            print(f"\nGet-Jobs which-jobs={which}: FAILED ({error})")
            continue
        print(f"\nGet-Jobs which-jobs={which}: {len(groups)} job(s)")
        if groups:
            print("  attributes on the newest:")
            for key, attribute in sorted(groups[-1].items()):
                value = attribute.values if len(attribute.values) > 1 else attribute.value
                print(f"    {key} = {value!r}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} <printer-host>   (tried paths: {', '.join(COMMON_PATHS)})")
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
