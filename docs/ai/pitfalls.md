# Pitfalls — traps that already cost time

## Reaching the LAN from this machine

macOS Local Network privacy grants LAN access per *responsible app*. The user's
shell (and `/usr/bin/python3`, `curl`) can reach the printer; the Claude Code
process and its uv/Homebrew Python often **cannot**, failing with `OSError:
[Errno 65] No route to host` — even with the Bash sandbox disabled.

- Don't diagnose this as routing, DNS, a firewall, or a sleeping printer without
  checking. Confirm the name resolves (`dscacheutil -q host -a name <name>`), then
  either ask the user to run the command, or use the system-Python shim
  (testing.md). A device that resolves and the user can ping is reachable — the
  block is on our side.

## Printer quirks (each found by real hardware, not fixtures)

- **Clock offset is common.** HP M880 and Brother MFC-L3770CDW show the right time
  on their panel but stamp IPP timestamps with a stale UTC offset (an hour off in
  summer). Detect via `printer-current-time` (`clock.measure`), correct at ingest,
  keep raw. **Do not** derive the correction from a job's completion time — a
  stale job underestimates the offset and once clobbered the exact reading to zero.
- **Wall-clock jitter.** A Brother returned a `date-time-at-creation` that moved a
  second between two reads of the *same* job. That's why `job_key` keys on
  `time-at-creation` (uptime), not the wall clock.
- **Sheets may be unreported.** A Brother never sends `job-media-sheets-completed`.
  Store `None`, not 0 — "didn't say" ≠ "used no paper". Totals show sheets only
  when every job in the group reported them.
- **`job-uuid` is usually zeroed.** All-zero UUID on ~everything except Mopria
  jobs, so it can't be the identity by itself.
- **Retention is tiny.** ~20 jobs on an M880, one or two on cheaper printers.
  Poll fast (default 15s) or lose jobs silently; the rollover warning is truthful.
- **Not every "printer" speaks IPP.** A Fritz!Box shares USB printers as raw
  socket on 9100 and answers 200-with-HTML on 80 — so **check the content type is
  `application/ipp` before decoding**, or the codec walks off the end (that HTML
  once crashed a run). Raw-9100/LPD-515 with no IPP means no job history exists to
  read; say so, don't invite a bug report.
- **Ports: refused vs silent.** Only a *refused* port proves the host is present
  with nothing there. Silence (timeout / EHOSTUNREACH) may be a sleeping printer,
  so the scan retries once when everything is unreachable (cold ARP cache →
  EHOSTUNREACH → the probe itself wakes it).

## Tests and CI

- **Time zone.** CI is UTC; any assertion on a rendered wall-clock time must not
  assume the runner's zone. `tests/conftest.py` pins Europe/Berlin.
- **No real sockets in unit tests.** A test that hit `192.0.2.x` (TEST-NET,
  black-holed) made the suite take 40s. Stub `port_state`/`urlopen`.
- **`multiprocessing` spawn + heredoc scripts don't mix** — spawned workers
  re-import `__main__` and fail on a stdin script. Use a real file for such tests.

## Self-discipline (the ones that were embarrassing)

- **Don't assert an unverified fix.** A commit claimed self-signed TLS was
  accepted when the edit had never reached the file; another asserted a buffering
  cause that couldn't be reproduced. Verify, then write it down — and if a comment
  or commit message is later found wrong, correct the code/comment, not just a
  follow-up commit message.
- **A `sed`/`str.replace` edit that doesn't match silently no-ops.** Several edits
  "succeeded" without changing anything because an anchor was slightly off. Check
  the result, not just the exit code.
- **Docker isn't always running here.** Verify the image separately (`docker build`
  then run) rather than assuming; CI also builds it on every push.
