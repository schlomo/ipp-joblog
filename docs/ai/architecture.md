# Architecture

A poll reads the printer's short job history over IPP, folds it into a per-printer
SQLite file, and (for `serve`) renders static HTML. Everything is small,
dependency-free, and testable without a printer.

## Modules (`src/ipp_joblog/`)

| Module | Responsibility |
| --- | --- |
| `ipp.py` | The IPP/2.0 codec (RFC 8010 encode/decode), the HTTP client, endpoint **discovery** (ports 631/80/443 × candidate paths, TLS unverified), and `Target`/`parse_target` (a host may be a name, `name:port`, or full URL). `port_state()` classifies a TCP probe. |
| `jobs.py` | `Job` (one print job) and `job_key()` — the stable identity. `PrinterJobLog` fetches and parses `Get-Jobs`. |
| `store.py` | `JobStore`: SQLite, migrations (`user_version`), WAL, per-user totals, the `printer_facts` key/value table, `PrinterSummary`. |
| `poller.py` | `Poller.poll()` — one cycle: fetch, detect rollover, measure the clock offset when there are new jobs, store at the corrected instant. Returns `PollResult`. |
| `clock.py` | Offset detection and correction. `measure()` reads `printer-current-time`; `correction_for()` rounds to a time-zone step with a drift guard. |
| `probe.py` | `probe()` → `ProbeReport` (is this printer usable?), and `collect_facts()` (the printer's self-description). |
| `diagnose.py` | Port scan + shape reading (`verdict`), full attribute dump (`report`), live-capture (`watch`), and the bug-report invitation. |
| `page.py` | `Dashboard` and `Overview` → self-contained HTML (no JS, no assets). |
| `output.py` | Text/CSV/JSON formatters and the poll-log `job_line`. Every function returns a string. |
| `serve.py` | The poll loop's page writer and a stdlib static file server. Atomic page writes. |
| `cli.py` | `Settings` (typed, from argparse), the command handlers, `notice()` (stderr), stdout/stderr discipline. |
| `version.py` | Version resolution: env var → build-time `_version.py` → package metadata. |

## Data model

- **One SQLite file per printer**, named after the host slug (`hpm880.sqlite3`),
  all inside a **state directory** (`--state-dir`). This is what makes multi-printer
  a scheduling problem, not a data-layout one.
- **`jobs` table**: columns are derived from the `Job` dataclass fields, plus
  `first_seen_at` and the **raw timestamp columns** (`created_at_raw`,
  `completed_at_raw`). `completed_at`/`created_at` hold the **corrected absolute
  instant**; the raw columns hold exactly what the printer sent. A drift test
  asserts the table matches this set.
- **`printer_facts` table**: key/value, so learning to read a new attribute never
  needs a migration.
- **Job identity** (`job_key`): real `job-uuid` → `time_at_creation`(uptime)+`job-id`
  → wall-clock+`job-id` → bare `job-id`. Uptime is preferred over the wall clock
  because some printers jitter the wall clock between reads. The raw inputs are
  stored so a future key change can recompute existing rows instead of stranding them.

## How a `serve` runs

1. `connect()` — scan ports, discover the IPP endpoint (or use a pinned URL).
2. `learn_about_printer()` — store the printer's facts; note once if it won't
   report its clock.
3. Loop: `Poller.poll()` → measure offset (only when new jobs) → store corrected
   instants → rewrite this printer's page and `index.html` → sleep `--interval`.
4. A stdlib server hands out the directory. Pages are written atomically
   (`mkstemp` + rename), so a reader never sees a half-written file, and several
   pollers can share one directory (WAL for the DBs, unique temp files for pages).

## Output discipline

`stdout` is data (reports, CSV, the probe verdict); `stderr` is everything else
(progress, poll log, warnings, errors) via `notice()`. So `report --format csv >
file` is clean, and a container log stays readable.
