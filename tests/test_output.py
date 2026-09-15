from __future__ import annotations

import json
from datetime import UTC

from ipp_joblog.output import (
    TOTAL_COLUMNS,
    job_line,
    jobs_csv,
    totals_csv,
    totals_json,
    totals_table,
)
from ipp_joblog.store import UserTotals

TOTALS = [
    UserTotals("alice", jobs=9, impressions=250, sheets=125, color_impressions=176),
    UserTotals("bo", jobs=1, impressions=3, sheets=3, color_impressions=3),
]


def test_table_aligns_names_left_and_numbers_right():
    lines = totals_table(TOTALS).splitlines()
    assert lines[0].startswith("user")
    assert lines[1].startswith("alice")
    assert lines[2].startswith("bo ")  # short name padded, not shifted
    assert all(line.rstrip().endswith(("mono", "74", "0")) for line in lines)


def test_table_survives_having_no_rows():
    assert totals_table([]).splitlines() == ["user   jobs  pages sheets  color   mono"]


def test_csv_and_json_use_the_same_field_names():
    header = totals_csv(TOTALS).splitlines()[0].split(",")
    assert header == [column.attribute for column in TOTAL_COLUMNS]
    assert list(json.loads(totals_json(TOTALS))[0]) == header


def test_json_includes_the_derived_mono_column():
    first = json.loads(totals_json(TOTALS))[0]
    assert first["mono_impressions"] == 74
    assert first["impressions"] == 250


def test_jobs_csv_uses_the_database_columns():
    rows = [{"key": "k", "user_name": "alice", "impressions": 2}]
    assert jobs_csv(rows).splitlines() == ["key,user_name,impressions", "k,alice,2"]


def test_jobs_csv_still_emits_a_header_when_empty():
    assert jobs_csv([]).strip() == "key"


def test_job_line_shows_the_essentials(make_job):
    line = job_line(make_job(user_name="alice", impressions=72, sheets=36))
    assert "alice" in line and "72 pages" in line and "36 sheets" in line
    assert "mono" in line


def test_unknown_sheets_render_as_a_dash():
    totals = [UserTotals("alice", jobs=1, impressions=4, sheets=None, color_impressions=0)]
    table = totals_table(totals)
    assert "—" in table
    assert "alice" in table
    assert "0" not in table.splitlines()[1].split()[3]  # the sheets cell is not a zero


def test_the_dash_is_for_humans_only():
    """CSV leaves the field empty and JSON emits null; only text shows a dash."""
    import json

    totals = [UserTotals("alice", jobs=1, impressions=4, sheets=None, color_impressions=0)]
    assert "—" in totals_table(totals)
    assert "—" not in totals_csv(totals)
    assert totals_csv(totals).splitlines()[1] == "alice,1,4,,0,4"
    assert json.loads(totals_json(totals))[0]["sheets"] is None


def test_job_line_shows_a_dash_for_unreported_sheets(make_job):
    assert "— sheets" in job_line(make_job(sheets=None))


def test_job_lines_use_local_time(make_job):
    from datetime import datetime

    completed = datetime(2026, 9, 15, 14, 21, 21, tzinfo=UTC)
    line = job_line(make_job(completed_at=completed))
    assert completed.astimezone().strftime("%Y-%m-%d %H:%M") in line
