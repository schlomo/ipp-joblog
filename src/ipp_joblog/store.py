"""Durable job history.

The printer remembers only its last handful of jobs, so every poll folds that
snapshot into a SQLite file that keeps the lot.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass, fields
from datetime import datetime, timedelta
from pathlib import Path

from ipp_joblog import clock
from ipp_joblog.jobs import MONOCHROME_MODES, Job

# One column per Job field, in the same order, plus when we first saw the job.
JOB_COLUMNS = tuple(field.name for field in fields(Job))
SEEN_COLUMN = "first_seen_at"

# The v1 table. Frozen history: migrations below have already run against it,
# so it is never edited -- later shapes are expressed as further migrations.
CREATE_JOBS = """
CREATE TABLE IF NOT EXISTS jobs (
    key              TEXT PRIMARY KEY,
    job_id           INTEGER NOT NULL,
    job_name         TEXT NOT NULL,
    user_name        TEXT NOT NULL,
    state            TEXT NOT NULL,
    impressions      INTEGER NOT NULL,
    sheets           INTEGER NOT NULL,
    color_mode       TEXT NOT NULL,
    sides            TEXT NOT NULL,
    document_format  TEXT NOT NULL,
    created_at       TEXT,
    completed_at     TEXT,
    first_seen_at    TEXT NOT NULL
)
"""

# Ordered schema steps. The database records how many have run in SQLite's
# `user_version`, so opening an older file applies only what it is missing.
#
# To change the schema: append a new tuple. Never edit or reorder an existing
# one -- databases in the wild have already run it. Adding a field to Job means
# adding its column here too, which test_schema_matches_the_job_dataclass
# enforces.
MIGRATIONS: tuple[tuple[str, ...], ...] = (
    (
        CREATE_JOBS,
        "CREATE INDEX IF NOT EXISTS jobs_user_completed ON jobs (user_name, completed_at)",
        "CREATE INDEX IF NOT EXISTS jobs_completed ON jobs (completed_at)",
    ),
    (
        # Keep the raw inputs to job_key so a later change to key derivation can
        # rewrite existing rows rather than leaving them under stale keys.
        "ALTER TABLE jobs ADD COLUMN job_uuid TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE jobs ADD COLUMN time_at_creation INTEGER NOT NULL DEFAULT 0",
    ),
    (
        # What the printer says about itself. Key/value so that learning to read
        # another attribute never needs a migration.
        "CREATE TABLE IF NOT EXISTS printer_facts ( name TEXT PRIMARY KEY, value TEXT NOT NULL)",
    ),
    (
        # Make sheets nullable. Some printers never report it, and storing zero
        # made "did not say" indistinguishable from "used no paper". SQLite
        # cannot relax NOT NULL in place, so the table is rebuilt -- which is
        # also the first migration to prove that heavier changes work.
        "ALTER TABLE jobs RENAME TO jobs_old",
        # Spelled out rather than derived from CREATE_JOBS, which is the v1
        # shape and knows nothing of the columns v2 added.
        """
        CREATE TABLE jobs (
            key              TEXT PRIMARY KEY,
            job_id           INTEGER NOT NULL,
            job_name         TEXT NOT NULL,
            user_name        TEXT NOT NULL,
            state            TEXT NOT NULL,
            impressions      INTEGER NOT NULL,
            sheets           INTEGER,
            color_mode       TEXT NOT NULL,
            sides            TEXT NOT NULL,
            document_format  TEXT NOT NULL,
            created_at       TEXT,
            completed_at     TEXT,
            first_seen_at    TEXT NOT NULL,
            job_uuid         TEXT NOT NULL DEFAULT '',
            time_at_creation INTEGER NOT NULL DEFAULT 0
        )
        """,
        "INSERT INTO jobs (key, job_id, job_name, user_name, state, impressions, sheets,"
        " color_mode, sides, document_format, created_at, completed_at, first_seen_at,"
        " job_uuid, time_at_creation)"
        " SELECT key, job_id, job_name, user_name, state, impressions,"
        # A finished job with pages cannot have used no sheets, so that zero was
        # a printer staying silent: recover it as unknown.
        " CASE WHEN sheets = 0 AND impressions > 0 THEN NULL ELSE sheets END,"
        " color_mode, sides, document_format, created_at, completed_at, first_seen_at,"
        " job_uuid, time_at_creation FROM jobs_old",
        "DROP TABLE jobs_old",
        "CREATE INDEX IF NOT EXISTS jobs_user_completed ON jobs (user_name, completed_at)",
        "CREATE INDEX IF NOT EXISTS jobs_completed ON jobs (completed_at)",
    ),
)
SCHEMA_VERSION = len(MIGRATIONS)


class StoreVersionError(RuntimeError):
    """The database was written by a newer ipp-joblog than this one."""


class DatabaseChoiceError(RuntimeError):
    """The state directory does not say which printer was meant."""


DB_SUFFIX = ".sqlite3"
BUSY_TIMEOUT = 15.0  # seconds a write waits for another process to finish


def database_name(host: str) -> str:
    """The database file for one printer, named after how you address it.

    ``hpm880`` and ``brw0011223344ff.local.`` become ``hpm880.sqlite3`` and
    ``brw0011223344ff.local.sqlite3``, so several printers coexist in one state
    directory without being told about each other.
    """
    slug = re.sub(r"[^a-z0-9._-]+", "-", host.strip().lower()).strip("-.")
    return f"{slug or 'printer'}{DB_SUFFIX}"


def databases(state_dir: Path) -> list[Path]:
    """Every printer database already in this state directory."""
    return sorted(state_dir.glob(f"*{DB_SUFFIX}")) if state_dir.is_dir() else []


def resolve_database(state_dir: Path, host: str | None) -> Path:
    """Pick the database to work with.

    With a host it is simply that printer's file, whether or not it exists yet.
    Without one -- the reporting commands do not need a printer -- it is the
    only database present, and an error if that is ambiguous.
    """
    if host:
        return state_dir / database_name(host)
    found = databases(state_dir)
    if len(found) == 1:
        return found[0]
    if not found:
        raise DatabaseChoiceError(f"no printer database in {state_dir}; poll a printer first")
    names = ", ".join(path.name for path in found)
    raise DatabaseChoiceError(
        f"{len(found)} printer databases in {state_dir} ({names}); pass --host to choose one"
    )


# The same rule as Job.is_color, expressed for SQLite so totals can be summed
# in one query. MONOCHROME_MODES is the single source of truth for both.
_MONO_LIST = ", ".join(f"'{mode}'" for mode in MONOCHROME_MODES)
_COLOR_IMPRESSIONS = f"SUM(CASE WHEN color_mode NOT IN ({_MONO_LIST}) THEN impressions ELSE 0 END)"


@dataclass(frozen=True, slots=True)
class PrinterSummary:
    """One printer's row on the overview page, read entirely from its database."""

    slug: str
    facts: dict[str, str]
    users: int
    jobs: int
    pages: int
    sheets: int | None
    last_job_at: str | None
    correction: timedelta = timedelta()

    @property
    def host(self) -> str:
        return self.facts.get("host", self.slug)

    @property
    def polled(self) -> bool:
        """False for a database no version of this tool has ever polled."""
        return bool(self.facts)

    @property
    def model(self) -> str:
        if not self.polled:
            return "never polled — no printer details recorded"
        return self.facts.get("printer-make-and-model", "model not reported")

    @property
    def page(self) -> str:
        """The detail page for this printer, named after its database."""
        return f"{self.slug}.html"


@dataclass(frozen=True, slots=True)
class UserTotals:
    """What one user printed over the reported window."""

    user_name: str
    jobs: int
    impressions: int
    sheets: int | None
    color_impressions: int

    @property
    def mono_impressions(self) -> int:
        return self.impressions - self.color_impressions


def add_sheets(counts: Iterable[int | None]) -> int | None:
    """Total sheets, or ``None`` if any part of the total is unknown."""
    known = list(counts)
    return None if any(count is None for count in known) else sum(known)


def _column_value(job: Job, column: str) -> object:
    """Datetimes go in as ISO text; everything else as-is."""
    value = getattr(job, column)
    return value.isoformat() if isinstance(value, datetime) else value


class JobStore:
    """SQLite-backed job history. Re-inserting a known job is a no-op."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        #: True when this open created the file rather than reusing one.
        self.created = not self.path.exists()
        #: Migration numbers applied during this open, in order. The caller
        #: reports them; the store itself writes nothing to the terminal.
        self.applied_migrations: list[int] = []
        self._connection = sqlite3.connect(self.path, timeout=BUSY_TIMEOUT)
        self._connection.row_factory = sqlite3.Row
        # The overview reads every printer's database while their own pollers
        # write to them. WAL lets those readers through instead of making them
        # wait out each write. It is a property of the file, so setting it once
        # is enough; a filesystem that cannot do WAL keeps its previous mode.
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._connection.execute("PRAGMA synchronous = NORMAL")
        self.migrate()

    @property
    def version(self) -> int:
        """How many migrations this database has had applied."""
        return self._connection.execute("PRAGMA user_version").fetchone()[0]

    def migrate(self) -> None:
        """Bring the database up to SCHEMA_VERSION, doing nothing if it is current.

        A database from a newer release is refused rather than written to, since
        this code cannot know what its columns mean.
        """
        applied = self.version
        if applied > SCHEMA_VERSION:
            raise StoreVersionError(
                f"{self.path} was written by a newer ipp-joblog "
                f"(schema v{applied}, this build understands v{SCHEMA_VERSION}). Upgrade."
            )
        for number, statements in enumerate(MIGRATIONS[applied:], start=applied + 1):
            for statement in statements:
                self._connection.execute(statement)
            # PRAGMA cannot be parameterised; `number` is a loop counter, not input.
            self._connection.execute(f"PRAGMA user_version = {number}")
            self.applied_migrations.append(number)
        self._connection.commit()

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> JobStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _query(self, sql: str, parameters: Iterable[object] = ()) -> list[dict]:
        return [dict(row) for row in self._connection.execute(sql, tuple(parameters))]

    def _count(self, sql: str, parameters: Iterable[object] = ()) -> int:
        return self._connection.execute(sql, tuple(parameters)).fetchone()[0]

    def known_keys(self) -> set[str]:
        return {row["key"] for row in self._query("SELECT key FROM jobs")}

    def add(self, jobs: Iterable[Job], *, seen_at: datetime | None = None) -> list[Job]:
        """Insert jobs we have not stored yet and return exactly those."""
        seen = (seen_at or datetime.now().astimezone()).isoformat()
        known = self.known_keys()
        fresh = [job for job in jobs if job.key not in known]

        columns = ", ".join((*JOB_COLUMNS, SEEN_COLUMN))
        placeholders = ", ".join("?" * (len(JOB_COLUMNS) + 1))
        self._connection.executemany(
            f"INSERT OR IGNORE INTO jobs ({columns}) VALUES ({placeholders})",
            [(*(_column_value(job, column) for column in JOB_COLUMNS), seen) for job in fresh],
        )
        self._connection.commit()
        return fresh

    def is_rollover(self, jobs: list[Job]) -> bool:
        """True when the printer's history turned over completely between polls.

        A snapshot that shares no job at all with a non-empty store means the
        printer discarded jobs before we ever saw them.
        """
        if not jobs or not self._count("SELECT COUNT(*) FROM jobs"):
            return False
        keys = [job.key for job in jobs]
        placeholders = ", ".join("?" * len(keys))
        return not self._count(f"SELECT COUNT(*) FROM jobs WHERE key IN ({placeholders})", keys)

    @staticmethod
    def _window(since: datetime | None, until: datetime | None) -> tuple[str, list[str]]:
        """Build the optional ``WHERE`` clause limiting rows by completion time."""
        clauses, parameters = [], []
        if since:
            clauses.append("completed_at >= ?")
            parameters.append(since.isoformat())
        if until:
            clauses.append("completed_at < ?")
            parameters.append(until.isoformat())
        return (" WHERE " + " AND ".join(clauses)) if clauses else "", parameters

    def rows(self, *, since: datetime | None = None, until: datetime | None = None) -> list[dict]:
        """Stored jobs, oldest first."""
        where, parameters = self._window(since, until)
        return self._query(f"SELECT * FROM jobs{where} ORDER BY completed_at", parameters)

    def facts(self) -> dict[str, str]:
        """What the printer last told us about itself."""
        return {row["name"]: row["value"] for row in self._query("SELECT * FROM printer_facts")}

    def remember_facts(self, facts: dict[str, str]) -> None:
        """Record the printer's self-description, replacing what was there."""
        self._connection.executemany(
            "INSERT INTO printer_facts (name, value) VALUES (?, ?)"
            " ON CONFLICT(name) DO UPDATE SET value = excluded.value",
            sorted(facts.items()),
        )
        self._connection.commit()

    def clock_correction(self) -> timedelta:
        """The stored shift for this printer's misreported clock, or none."""
        return clock.stored_correction(self.facts())

    def set_clock_correction(self, correction: timedelta) -> None:
        self.remember_facts({clock.KEY: clock.as_fact(correction)})

    def summarise(self) -> PrinterSummary:
        """Everything the overview page needs about this printer."""
        totals = self.totals_by_user()
        newest = self.recent(1)
        correction = self.clock_correction()
        return PrinterSummary(
            slug=self.path.name.removesuffix(DB_SUFFIX),
            facts=self.facts(),
            users=len(totals),
            jobs=sum(total.jobs for total in totals),
            pages=sum(total.impressions for total in totals),
            sheets=add_sheets(total.sheets for total in totals),
            last_job_at=clock.apply_iso(newest[0]["completed_at"], correction) if newest else None,
            correction=correction,
        )

    def recent(self, limit: int = 50) -> list[dict]:
        """The most recently completed jobs, newest first."""
        return self._query("SELECT * FROM jobs ORDER BY completed_at DESC LIMIT ?", (limit,))

    def totals_by_user(
        self, *, since: datetime | None = None, until: datetime | None = None
    ) -> list[UserTotals]:
        """Per-user page counts, busiest user first."""
        where, parameters = self._window(since, until)
        rows = self._query(
            "SELECT user_name, COUNT(*) AS jobs, SUM(impressions) AS impressions,"
            # Sheets are reported only when every job in the group reported them.
            " CASE WHEN COUNT(sheets) = COUNT(*) THEN SUM(sheets) END AS sheets,"
            f" {_COLOR_IMPRESSIONS} AS color_impressions"
            f" FROM jobs{where} GROUP BY user_name ORDER BY impressions DESC, user_name",
            parameters,
        )
        return [
            UserTotals(
                user_name=row["user_name"],
                jobs=row["jobs"],
                impressions=row["impressions"] or 0,
                sheets=row["sheets"],
                color_impressions=row["color_impressions"] or 0,
            )
            for row in rows
        ]
