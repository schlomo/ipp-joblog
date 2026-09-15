"""Rendering stored data for a terminal: aligned text, CSV and JSON.

Every function returns a string. Nothing here prints or hands a csv.writer back
to its caller, which keeps the commands in :mod:`ipp_joblog.cli` down to
"fetch it, render it, print it".
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Sequence
from dataclasses import dataclass

from ipp_joblog.jobs import Job
from ipp_joblog.store import UserTotals

# Shown in text output where a printer did not report a figure, so it never
# reads as zero. CSV leaves the cell empty and JSON emits null instead.
UNKNOWN = "—"


@dataclass(frozen=True, slots=True)
class Column:
    """One reported column: its heading, where the value comes from, how it sits."""

    heading: str
    attribute: str
    align: str = "right"
    min_width: int = 6

    def of(self, total: UserTotals) -> object:
        """The raw value: JSON wants null and CSV wants empty, not a dash."""
        return getattr(total, self.attribute)

    def shown(self, total: UserTotals) -> str:
        """The value as a human reads it, where nothing reported is a dash."""
        value = self.of(total)
        return UNKNOWN if value is None else str(value)

    def cell(self, text: str, width: int) -> str:
        return text.ljust(width) if self.align == "left" else text.rjust(width)


# Text, CSV and JSON all read this, so a new column is a one-line change.
TOTAL_COLUMNS = (
    Column("user", "user_name", align="left", min_width=4),
    Column("jobs", "jobs"),
    Column("pages", "impressions"),
    Column("sheets", "sheets"),
    Column("color", "color_impressions"),
    Column("mono", "mono_impressions"),
)


def _csv(header: Sequence[str], rows: Sequence[Sequence[object]]) -> str:
    out = io.StringIO()
    writer = csv.writer(out)  # None becomes an empty field, which is CSV for "missing"
    writer.writerow(header)
    writer.writerows(rows)
    return out.getvalue()


def job_line(job: Job) -> str:
    """One line per job, for the poll and watch logs."""
    # astimezone(): printers report in their own zone, and a Brother uses UTC.
    when = job.completed_at.astimezone().strftime("%Y-%m-%d %H:%M") if job.completed_at else "?"
    colour = "color" if job.is_color else "mono"
    sheets = UNKNOWN if job.sheets is None else str(job.sheets)
    return (
        f"{when}  {job.user_name:<16} {job.impressions:>5} pages "
        f"{sheets:>5} sheets  {colour:<5} {job.state:<9} {job.job_name[:48]}"
    )


def totals_table(totals: Sequence[UserTotals]) -> str:
    """Per-user totals as an aligned table."""
    headings = [column.heading for column in TOTAL_COLUMNS]
    rows = [[column.shown(total) for column in TOTAL_COLUMNS] for total in totals]
    widths = [
        max([column.min_width, len(column.heading), *(len(row[index]) for row in rows)])
        for index, column in enumerate(TOTAL_COLUMNS)
    ]

    def line(cells: Sequence[str]) -> str:
        return " ".join(
            column.cell(cells[index], widths[index]) for index, column in enumerate(TOTAL_COLUMNS)
        )

    return "\n".join(line(cells) for cells in [headings, *rows])


def totals_csv(totals: Sequence[UserTotals]) -> str:
    return _csv(
        [column.attribute for column in TOTAL_COLUMNS],
        [[column.of(total) for column in TOTAL_COLUMNS] for total in totals],
    )


def totals_json(totals: Sequence[UserTotals]) -> str:
    return json.dumps(
        [{column.attribute: column.of(total) for column in TOTAL_COLUMNS} for total in totals],
        indent=2,
    )


def jobs_csv(rows: Sequence[dict]) -> str:
    """Stored job rows as CSV, one column per database column."""
    if not rows:
        return _csv(["key"], [])
    columns = list(rows[0])
    return _csv(columns, [[row[column] for column in columns] for row in rows])
