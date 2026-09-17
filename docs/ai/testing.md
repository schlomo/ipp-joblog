# Testing and verifying against a real printer

## The suite

```bash
uv run pytest          # 218 tests, no printer required
uvx ruff check . && uvx ruff format --check .
```

Tests run entirely offline. The IPP codec is exercised against a recorded
`Get-Jobs` response in `tests/fixtures/get-jobs-completed.ipp` (sanitised —
placeholder users/hostnames, real page counts). The HTTP server is tested with a
real socket on `127.0.0.1`; concurrency with real processes and threads.

Keep tests hermetic: no real LAN sockets (stub `port_state`/`urlopen`), no
wall-clock or time-zone assumptions (conftest pins the zone), no `multiprocessing`
in a heredoc.

## Verifying against the live printer

The tool runs on Python 3.11 via uv, but **uv's Python can't reach the LAN on
this Mac** (see pitfalls → Reaching the LAN). Two ways to get real-hardware proof:

1. **Ask the user to run it.** Their `uv run ipp-joblog …` reaches the printer.
   This is the primary method — the user has been an active co-driver.

2. **System-Python shim.** `/usr/bin/python3` *can* reach the LAN but is 3.9, so
   it can't run 3.11 syntax directly. Copy the package and down-level it:

   ```bash
   DEST=/tmp/e2e; rm -rf "$DEST"; mkdir -p "$DEST"; cp -r src/ipp_joblog "$DEST/"
   sed -i '' 's/, slots=True//' "$DEST"/ipp_joblog/*.py
   sed -i '' 's/from datetime import UTC, datetime/from datetime import datetime, timezone\nUTC = timezone.utc/' "$DEST"/ipp_joblog/*.py
   PYTHONPATH="$DEST" /usr/bin/python3 -m ipp_joblog --host <printer> probe
   ```

   Fragile (breaks on newer syntax), but it has caught real bugs the fixtures
   couldn't — clock offsets, jitter, the Fritz!Box HTML crash. Prefer method 1
   when the change is subtle.

## Real hardware seen this far

| Printer | Result | Taught us |
| --- | --- | --- |
| HP Color LaserJet flow MFP M880 | works | reports `printer-current-time` (exact offset); local-time reporting; ~20-job retention |
| Brother MFC-L3770CDW | works | UTC reporting; wall-clock jitter → key on uptime; never reports sheets; brief retention |
| Epson L3150 (user report) | n/a | refused all IPP ports while printing over CUPS — likely raw/9100 |
| AVM Fritz!Box (USB printer) | no | raw socket on 9100, no IPP; served HTML that crashed the decoder before the content-type guard |

When a new printer is tried, `ipp-joblog probe` (and `diagnose` if it fails) is
the report to capture — add the outcome to this table.

## Docker

`docker build` isn't guaranteed to run in-session (the daemon is often down).
Build and run it explicitly to verify; CI builds it on every push regardless.
The image is Alpine + its own `python3` (no pip — there are no deps) and carries
its version via a build arg.
