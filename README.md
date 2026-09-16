# ipp-joblog

**Who printed how much, straight from the printer. No print server, no drivers,
no agents, no client configuration.**

AirPrint decentralised printing: every phone and laptop talks to the printer
directly, and the print server that used to do the accounting is gone. But the
printer still counts every page — and it will tell you, over standard IPP, if
you ask.

`ipp-joblog` asks, every minute, and keeps the answers:

```
user          jobs    pages   sheets    color     mono
alice            9      250      125      176       74
bob             10       28       17        9       19
SM-A146P         1        3        3        3        0
```

Zero runtime dependencies beyond Python 3.11 or later. Zero printer configuration. No credentials needed. Read-only: it only ever
issues IPP `Get-*` operations.

## Quick start

```bash
export IPP_PRINTER_HOST=192.168.1.50
ipp-joblog probe     # can this printer be accounted for?
ipp-joblog serve     # poll forever, dashboard on http://localhost:8080
```

That is the whole setup. `serve` writes self-contained HTML after every poll
and hands it out with a stdlib static file server: no templates, no JavaScript,
no assets to fetch.

Each printer gets a page named after its database — `hpm880.sqlite3` is served
as `hpm880.html` — showing its per-user totals, recent jobs, and what the
printer says about itself. `index.html` lists every printer in the state
directory and links to them, so pointing one `serve` at each printer gives you
one overview of all of them.

## Install

### Docker (recommended)

One container, one volume, nothing else:

```bash
docker run -d --name ipp-joblog --restart unless-stopped \
  -e IPP_PRINTER_HOST=192.168.1.50 -e TZ=Europe/Berlin \
  -p 8080:8080 -v ipp-joblog:/data \
  ghcr.io/schlomo/ipp-joblog:latest
```

Set `TZ` or the dashboard stamps itself in UTC while job times keep the
printer's own offset.

Or with the bundled [`compose.yaml`](compose.yaml): `docker compose up -d`.

The image is Alpine plus its own `python3` — no pip, no wheels, no virtualenv,
because there is nothing to install. State lives in the `/data` volume.

### Anywhere: pip or uv

```bash
pip install git+https://github.com/schlomo/ipp-joblog
# or, if you prefer an isolated tool install:
uv tool install git+https://github.com/schlomo/ipp-joblog
```

Plain `pip` is enough — there are no runtime dependencies, so nothing is
resolved or compiled. There is no PyPI package and no Homebrew tap; GitHub is
the only source. Pin a release with `@v1.0.42` to stay on one.

### Linux: systemd, without a container

Install the CLI system-wide, then drop in the
[unit file](packaging/systemd/ipp-joblog.service):

```bash
sudo pip install --break-system-packages git+https://github.com/schlomo/ipp-joblog
sudo cp packaging/systemd/ipp-joblog.service /etc/systemd/system/
echo 'IPP_PRINTER_HOST=192.168.1.50' | sudo tee /etc/default/ipp-joblog
sudo systemctl enable --now ipp-joblog
```

The install has to be system-wide: the unit runs `/usr/local/bin/ipp-joblog`,
and a per-user install lands in `~/.local/bin`, which the service cannot reach
— it runs under `DynamicUser=` with no home. With `uv` instead of pip, the
equivalent is
`sudo env "PATH=$PATH" UV_TOOL_BIN_DIR=/usr/local/bin uv tool install …`.
`DynamicUser=` and `StateDirectory=` mean there is no account to create and no
directory to chown; state lands in `/var/lib/ipp-joblog`.

### From a checkout

```bash
uv sync && uv run ipp-joblog probe
```

## Commands

| Command | What it does |
| --- | --- |
| `probe` | Reports every endpoint it tries and every attribute the printer returns, then says whether the printer can be accounted for. Run this first. If the answer is no, it dumps the full diagnostic itself and tells you where to report it. |
| `diagnose` | Everything a printer will say, for a bug report. `--watch 90` instead polls hard while you print, for printers that retain no finished jobs. |
| `serve` | Poll on an interval, write the dashboard, serve it over HTTP. |
| `watch` | Poll on an interval and log new jobs. `--html-dir` also writes the page, for serving behind your own web server. |
| `poll` | Fetch once and store new jobs. Safe to re-run; ideal for cron. |
| `report` | Per-user totals as text, `--format csv`, or `--format json`. `--since 30d`. |
| `jobs` | Every stored job as CSV. |

### More than one printer

`serve` polls *and* serves, so two of them would fight over the port. Give one
printer the web server and the others a `watch`, pointed at the same state and
page directories:

```bash
ipp-joblog --state-dir /var/lib/ipp-joblog --host hpm880 serve &
ipp-joblog --state-dir /var/lib/ipp-joblog --host brw0011223344ff.local. \
  watch --html-dir /var/lib/ipp-joblog/public &
```

Both write their own page and rebuild `index.html` from every database, so the
one web server publishes all of them. Under systemd or Docker, run one unit or
container per printer with the same state directory and only one of them
serving.

Several pollers can share one state directory: each writes only its own
printer's database and page, and `index.html` is published through a rename, so
a browser always gets a whole page even while four processes rewrite it.
Databases are WAL, so the overview can read a printer that is being polled
without waiting for it. Two pollers pointed at the *same* printer is harmless
too — the second one simply stores nothing new.

**stdout is data, stderr is everything else.** Reports and CSV go to stdout;
progress, poll logs, schema upgrades, warnings and errors go to stderr. So
`ipp-joblog report --format csv > usage.csv` captures exactly the CSV, and
`ipp-joblog serve` can have its log redirected without losing anything.

All state lives in one directory (`--state-dir`, or `IPP_JOBLOG_STATE_DIR`),
holding **one database per printer**, named after the host you poll —
`hpm880.sqlite3`, `brw0011223344ff.local.sqlite3`. So `report` and `jobs` work
against whatever `serve`, `watch` or `poll` has collected: run them side by
side, from cron, or from another shell while the poller keeps running.

`report` and `jobs` do not need `--host` when the directory holds a single
printer; with several, they say so and ask which one you meant.

## Configuration

Everything has a flag and an environment variable:

| Variable | Flag | Default |
| --- | --- | --- |
| `IPP_PRINTER_HOST` | `--host` | *required* — a name, `name:port`, or a full URL |
| `IPP_JOBLOG_STATE_DIR` | `--state-dir` | `.` |
| `IPP_PATH` | `--path` | discovered; overrides the path in `--host` |
| `IPP_JOBLOG_HTML_DIR` | `--html-dir` | `<state-dir>/public` |
| `IPP_JOBLOG_PORT` | `--port` | `8080` |
| `IPP_JOBLOG_BIND` | `--bind` | all interfaces |

The dashboard has no authentication — it is a LAN status page. Bind it to
`127.0.0.1` and put a reverse proxy in front if that is not what you want.

## Why the poller has to keep running

Printers remember only their last handful of finished jobs — 20 on the HP M880
this was built against. Each poll folds the printer's snapshot into SQLite and
skips jobs it already has, so re-running is always safe and the history grows
without bound while the printer's does not.

If a poll finds no overlap with what is already stored, the printer's history
rolled over completely between polls and jobs were lost — `ipp-joblog` says so
on stderr. One poll per minute is ample for a printer that retains a couple of
dozen jobs.

The default poll is every **15 seconds**, chosen for the worst case rather than
the best: a printer that keeps only the last job or two loses work between
one-minute polls, and it loses it silently. The cost is a few small HTTP
requests a minute to a device whose network stack has to be listening for print
jobs anyway.

On a short-memory printer, expect the rollover warning whenever two jobs land
between polls. There it is telling the truth.

## Does this work on any IPP printer?

The protocol is standard; what varies is what a given printer chooses to
implement. Run `probe` before trusting a new device — it prints every endpoint
it tries and every attribute it found:

```
$ uv run ipp-joblog probe
probing 5 candidate endpoints on printer.example.lan:631
  fail http://printer.example.lan:631/ipp/print  (GET_PRINTER_ATTRIBUTES failed with IPP status 0x0406)
  ok   http://printer.example.lan:631/ipp/port1
using IPP endpoint: ipp://printer.example.lan:631/ipp/port1
printer: HP Color LaserJet flow MFP M880
asking ipp://... for completed jobs (Get-Jobs, which-jobs=completed)
retained completed jobs: 20
  yes  job-originating-user-name  (required)
  yes  job-impressions-completed  (required)
  yes  job-media-sheets-completed  (optional)
  ...
This printer can be accounted for.
```

Three things are not guaranteed by the standard:

1. **Job history is optional.** `Get-Jobs` with `which-jobs=completed`
   (RFC 8011 §4.2.6) returns only jobs the printer chose to *retain*. Plenty of
   printers retain none, and then nothing here can help. `probe` exits non-zero
   and says so.
2. **The page-count attributes are optional.** `job-originating-user-name` is
   required by RFC 8011, but `job-impressions-completed` and
   `job-media-sheets-completed` are not, and `print-color-mode` comes from
   PWG 5100.13 rather than the core spec. Missing attributes degrade to zero
   rather than crashing; `probe` lists which ones a printer actually reports.
3. **The endpoint is vendor-specific**, in both halves. `/ipp/print` covers
   AirPrint and IPP Everywhere devices; CUPS queues and older firmware use
   something else, and a few printers answer IPP on their web port rather than
   631. Both are discovered: the ports are scanned, then the candidate paths
   tried on whichever could be listening.

   You never have to configure this, but you can. `--host` takes a bare name, a
   `name:port`, or a whole URL — so the device URI that `lpstat -v` prints for a
   CUPS queue can be pasted in unchanged:

   ```bash
   ipp-joblog --host ipp://printer.example:631/ipp/print probe
   ipp-joblog --host ipps://printer.example/ipp/print probe   # TLS
   ```

   TLS certificates are not verified. Printers present self-signed ones
   universally, so verifying would reject every printer there is. Nothing is
   given up by that here: this tool only reads, sends no credentials, and
   trusts nothing it receives beyond parsing it.

   A complete address skips the scan; anything less just narrows it. The
   database is always named after the host, so however you write the address it
   stays one printer.

When a printer cannot be reached, `probe` scans the ports that say what it is —
631, 80, 443, 9100 and 515 — and reads the shape. A printer that takes data on
9100 or 515 but refuses IPP is being fed raw bytes: it is told nothing about who
printed, so there is no per-user history on the device for anything to read, and
the report says so rather than leaving you to work it out. IPP closed with the
web interface open usually means IPP or AirPrint is switched off in its settings.
Everything refused usually means the address is no longer the printer.

Job identity adapts too: a real `job-uuid` when the printer sets one, else
printer uptime plus `job-id`, else the creation timestamp plus `job-id`, else
the bare `job-id`.

Uptime is preferred over the wall clock on purpose. A Brother MFC-L3770CDW
returns a `date-time-at-creation` that moves by a second between two reads of
the *same* job while `time-at-creation` stays fixed; keying on the wall clock
there would mint a new key every poll and count the job again each time.

Everything outside the tested configuration is standards-conformant but
unverified.

## When you want a real print server instead

Interposing a print server is the better architecture if you can adopt it. A
queue that clients authenticate against knows *who* printed rather than
guessing from a device name, keeps unlimited history, and can enforce quotas
and pull-printing. [SavaPage](https://www.savapage.org/) is the open-source
option, PaperCut the commercial one; on a CUPS server,
[`page_log`](https://openprinting.github.io/cups/doc/accounting.html) already
records pages per job.

`ipp-joblog` is for the other case: clients AirPrint straight at the printer
and you do not want a server in the path at all.

## Why this does not run on the printer

The obvious question: the printer already counts the pages and has a web server
and a hard disk — why does this need a host at all?

Because the door is locked commercially rather than technically. HP FutureSmart
devices really can host what you would want: a solution bundle may contribute a
"custom internal page that displays on the EWS", and installed solutions persist
on the device across reboots. But such a page only ever arrives inside an
HP-signed `.bdl` bundle installed through the EWS Solution Installer. The
device's whitelisting refuses third-party solution firmware whose signature does
not validate, and that enforcement is not something an administrator can switch
off. The APIs involved live in HP's OXP/OXPd SDK, which is not public: access
requires a *company* to join the HP JetAdvantage Solutions & Partners Program,
with accreditation and certification testing.

Some things that sound like a way in, and are not:

- **Uploading your own CA.** The EWS certificate store is a TLS trust store —
  used for validating connections, OCSP and e-mail signing. It is not a
  code-signing trust anchor, so it does not make an unsigned bundle installable.
  It *is* useful for pointing the printer at your own HTTPS service without a
  publicly-issued certificate.
- **Developer mode.** HP does operate one, but it belongs to HP Workpath, the
  Android-based platform on roughly 2019-and-later hardware. There is no
  documented developer mode, test-signing certificate or evaluation tier for
  FutureSmart 4 devices.
- **The device file system.** Reachable over PJL and PostScript, but it is raw
  storage: nothing placed there is served by the EWS, and HP recommends
  disabling that access.

So `ipp-joblog` runs beside the printer instead. To make the printer point at
it, add the dashboard under **General → Edit Other Links** in the EWS (five
slots), and it appears on every EWS page.

### Serverless Job Accounting, if your users will enter a code

Many FutureSmart devices, the M880 among them, have an HP-native on-device
per-user counter — **General → Job Statistics Settings → Device User Statistics
Log** — which needs no host at all. It is worth knowing about, but it attributes
jobs by *User Access Code* rather than by the name the client sends, and
AirPrint has no way to prompt for one. It also keeps cumulative counters with an
export-then-reset workflow rather than a history. If your users print from
phones and laptops without entering anything, that is the gap this tool fills.

## What it reads

| IPP attribute | Meaning |
| --- | --- |
| `job-originating-user-name` | who printed |
| `job-impressions-completed` | pages actually printed, **copies included** |
| `job-media-sheets-completed` | physical sheets (half the impressions when duplex), where reported |
| `print-color-mode` | `color` / `monochrome` |
| `date-time-at-completed` | timestamp with time zone |
| `printer-more-info` | a page the printer suggests, linked alongside its web interface |

`job-impressions-completed` counts copies: a 12-page document printed 6 times
reports 72 impressions and 36 sheets.

No authentication is needed — `Get-Jobs` answers unauthenticated on port 631.

## Caveats

- **Identity comes from the client.** iOS/macOS AirPrint sends the account
  short name (`alice`); Android/Mopria sends the *device* name (`SM-A146P`).
  Nothing on the printer can improve on that without user authentication.
- **Print jobs only.** IPP knows nothing about walk-up copies, faxes or USB
  printing. Those show up in a printer's web UI but not here.
- **Cancelled and aborted jobs are kept** with the pages they really consumed
  — an aborted 120-impression job still cost 120 impressions.
- **Not every printer counts sheets.** A Brother MFC-L3770CDW reports pages but
  never `job-media-sheets-completed`, so sheets show as `—` rather than `0`:
  "did not say" is not "used no paper". Text output shows a dash, CSV leaves the
  field empty, JSON emits `null`. A user's sheet total is only given when every
  one of their jobs reported it.
- **A printer's clock may lie even when it looks right.** A Brother
  MFC-L3770CDW showed the correct time on its own settings page while reporting
  job times an hour ahead over IPP: its firmware applies daylight saving to the
  display but not to the UTC offset it sends. `ipp-joblog` measures this — a job
  cannot finish after the moment it was read — and says so once per run. The
  workaround is to set the printer's time zone to the current *total* offset
  with automatic daylight saving off.
- **macOS Local Network privacy** can make Python fail with
  `[Errno 65] No route to host` even though `curl` works. Grant the terminal
  Local Network access under System Settings → Privacy & Security.

## Tested hardware, and what would help

| Printer | Works | Notes |
| --- | --- | --- |
| **HP Color LaserJet flow MFP M880** | yes | Written for and tested against this. Retains ~20 finished jobs, so the default one-minute poll is ample. |
| **Brother MFC-L3770CDW series** | yes, with a short leash | Reports user and page counts, but keeps a finished job only briefly — poll every 15s or so, and expect to miss jobs printed back to back. Its wall clock jitters (see below), which the job key works around. |

Everything the tool relies on is standard IPP, so it should work more widely
than the two printers above — but "should" is not "does". Run `probe` against
yours before trusting it.

If you run it against another printer, I would love to hear about it either
way, including a printer that does *not* work — a negative result saves the
next person the experiment. Paste the output of `ipp-joblog probe` into an
[issue](https://github.com/schlomo/ipp-joblog/issues): a success report tells
the next person their model works, and a failure report usually contains
exactly the attribute or endpoint that needs handling. Patches are just as
welcome.

## Development

```bash
uv sync
uv run pytest          # no printer required
uvx ruff check . && uvx ruff format --check .
```

The database migrates itself. SQLite's `user_version` records how many schema
steps a file has had, so an older database is upgraded in place on open, and
one written by a newer release is refused rather than corrupted. To change the
schema, append a tuple to `MIGRATIONS` in `store.py` — never edit an existing
one, because databases in the wild have already run it.

Versions are `1.0.<commit count>`, derived from git by `hatch-vcs`. Every green
push to `main` tags `v1.0.<n>`, publishes a multi-arch image to ghcr and cuts a
GitHub release. Nothing is bumped or published by hand; tags pushed with
`GITHUB_TOKEN` do not trigger workflows, so releasing cannot loop.

The IPP decoder is tested against a real recorded `Get-Jobs` response in
`tests/fixtures/`, with usernames, job names and hostnames replaced by
placeholders. The HTTP server is tested with a real request over a socket.

## License

MIT — see [LICENSE](LICENSE).
