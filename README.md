# ipp-joblog

**Who printed how much, straight from the printer. No print server, no drivers,
no agents, no client setup.**

AirPrint did away with the print server, and with it the per-user page counts it
used to keep. But the printer still counts every page, and it will tell you over
standard IPP. `ipp-joblog` asks on a timer and keeps the answers:

```
user          jobs    pages   sheets    color     mono
alice            9      250      125      176       74
bob             10       28       17        9       19
SM-A146P         1        3        3        3        0
```

No runtime dependencies beyond Python 3.11, no printer configuration, no
credentials. Read-only: it only ever issues IPP `Get-*` operations.

## Quick start

```bash
export IPP_PRINTER_HOST=192.168.1.50
ipp-joblog probe     # will this printer work? (run it first)
ipp-joblog serve     # poll forever; dashboard on http://localhost:8080
```

`serve` writes self-contained HTML after every poll and serves it from a stdlib
file server — no templates, no JavaScript, nothing to fetch. Each printer gets
its own page plus an `index.html` linking to them all.

## Install

**Docker** (recommended):

```bash
docker run -d --name ipp-joblog --restart unless-stopped \
  -e IPP_PRINTER_HOST=192.168.1.50 -e TZ=Europe/Berlin \
  -p 8080:8080 -v ipp-joblog:/data \
  ghcr.io/schlomo/ipp-joblog:latest
```

Or `docker compose up -d` with the bundled [`compose.yaml`](compose.yaml).

**pip or uv** — there is no PyPI package; install from GitHub:

```bash
pip install git+https://github.com/schlomo/ipp-joblog
```

For a native Linux service there is a [systemd unit](packaging/systemd/ipp-joblog.service)
in `packaging/` — install the CLI system-wide, drop the unit in, set
`IPP_PRINTER_HOST` in `/etc/default/ipp-joblog`.

## Commands

| Command | What it does |
| --- | --- |
| `probe` | Say whether a printer can be accounted for. Run it first; if not, it prints the full diagnostic and where to report it. |
| `serve` | Poll on an interval and serve the dashboard over HTTP. |
| `watch` | Poll on an interval and log new jobs (no web server). |
| `poll` | Fetch once and store new jobs. Safe to re-run; good for cron. |
| `report` | Per-user totals as text, `--format csv`, or `--format json`. `--since 30d`. |
| `jobs` | Every stored job as CSV. |
| `diagnose` | Dump everything a printer says, for a bug report. `--watch 90` catches jobs as they print. |

## Configuration

Everything has a flag and an environment variable:

| Variable | Flag | Default |
| --- | --- | --- |
| `IPP_PRINTER_HOST` | `--host` | *required* — a name, `name:port`, or a full URL |
| `IPP_JOBLOG_STATE_DIR` | `--state-dir` | `.` |
| `IPP_PATH` | `--path` | discovered |
| `IPP_JOBLOG_HTML_DIR` | `--html-dir` | `<state-dir>/public` |
| `IPP_JOBLOG_PORT` | `--port` | `8080` |
| `IPP_JOBLOG_BIND` | `--bind` | all interfaces |

`--host` accepts a plain name, `name:port`, or a full URL — so the device URI
that `lpstat -v` prints for a CUPS queue can be pasted in as-is. The port and
path are otherwise discovered.

The dashboard has no authentication; it is a LAN status page. Bind it to
`127.0.0.1` behind a reverse proxy if that matters.

## More than one printer

State lives in one directory, one SQLite file per printer, named after the host
you poll. `serve` both polls and serves, so give one printer the web server and
the rest a `watch` pointed at the same directories:

```bash
ipp-joblog --state-dir /var/lib/ipp-joblog --host hpm880 serve &
ipp-joblog --state-dir /var/lib/ipp-joblog --host officeprinter.local. \
  watch --html-dir /var/lib/ipp-joblog/public &
```

Each rebuilds `index.html` from every database, so the one server shows them all.
`report` and `jobs` need `--host` only when the directory holds more than one.

## Good to know

- **Run it continuously.** Printers remember only their last handful of finished
  jobs — 20 on an M880, one or two on some others. Each poll folds the snapshot
  into SQLite and skips what it already has, so re-running is always safe and the
  history grows without bound while the printer's does not. The default poll is
  every 15 seconds; raise `--interval` for a printer with a long memory.
- **Identity comes from the client.** iOS/macOS AirPrint sends the account name
  (`alice`); Android/Mopria sends the *device* name (`SM-A146P`). Nothing on the
  printer can do better without user authentication.
- **Print jobs only** — walk-up copies, faxes and USB printing leave no IPP job
  history.
- **Wrong clocks are corrected automatically.** Some printers report a stale UTC
  offset over IPP though their own display is right. `ipp-joblog` measures the
  error and shifts the shown times to match; stored timestamps stay as sent.

## Will it work on my printer?

Run `probe`. It reads only standard IPP, so it should work widely, but three
things a printer may not provide will stop it: retained job history, the
per-user page-count attributes, and reachability on a scanned port. `probe`
prints which, in plain language, and — when it can't — the diagnostic to attach
to an issue.

Tested against an **HP Color LaserJet flow MFP M880** and a **Brother
MFC-L3770CDW**. If you try another, a success or failure report either way is
welcome: paste `ipp-joblog probe` output into an
[issue](https://github.com/schlomo/ipp-joblog/issues).

## Development

```bash
uv sync
uv run pytest          # no printer required
uvx ruff check . && uvx ruff format --check .
```

Versions are `1.0.<commit count>`, derived from git. Every green push to `main`
tags a release and publishes a multi-arch image to ghcr.

## License

MIT — see [LICENSE](LICENSE).
