from __future__ import annotations

from datetime import datetime

import pytest

from ipp_joblog.store import JobStore


def test_add_returns_only_new_jobs(store, make_job):
    first = make_job()
    assert store.add([first]) == [first]
    assert store.add([first]) == []


def test_add_is_idempotent_across_polls(store, make_job):
    """Re-running a poll is the upgrade path, so replaying history must not double-count."""
    jobs = [make_job(), make_job(key="k2", job_id=20, impressions=2, sheets=1)]
    store.add(jobs)
    store.add(jobs)
    store.add(jobs)
    assert len(store.rows()) == 2
    assert store.totals_by_user()[0].impressions == 74


def test_totals_split_color_and_mono(store, make_job):
    store.add(
        [
            make_job(key="a", color_mode="monochrome", impressions=10, sheets=5),
            make_job(key="b", color_mode="color", impressions=4, sheets=2),
        ]
    )
    total = store.totals_by_user()[0]
    assert total.jobs == 2
    assert total.impressions == 14
    assert total.color_impressions == 4
    assert total.mono_impressions == 10
    assert total.sheets == 7


def test_totals_are_grouped_per_user_busiest_first(store, make_job):
    store.add(
        [
            make_job(key="a", user_name="bob", impressions=5),
            make_job(key="b", user_name="alice", impressions=50),
            make_job(key="c", user_name="SM-A146P", impressions=3),
        ]
    )
    assert [total.user_name for total in store.totals_by_user()] == ["alice", "bob", "SM-A146P"]


def test_window_filters_by_completion_time(store, make_job, cet):
    store.add(
        [
            make_job(key="old", completed_at=datetime(2026, 1, 1, 12, tzinfo=cet), impressions=100),
            make_job(key="new", completed_at=datetime(2026, 9, 10, 12, tzinfo=cet), impressions=7),
        ]
    )
    boundary = datetime(2026, 6, 1, tzinfo=cet)
    assert [total.impressions for total in store.totals_by_user(since=boundary)] == [7]
    assert store.totals_by_user(until=boundary)[0].impressions == 100


def test_is_rollover_needs_a_snapshot_that_shares_nothing(store, make_job):
    assert store.is_rollover([make_job(key="a")]) is False  # empty store loses nothing
    store.add([make_job(key="a")])
    assert store.is_rollover([make_job(key="a"), make_job(key="b")]) is False
    assert store.is_rollover([make_job(key="x"), make_job(key="y")]) is True
    assert store.is_rollover([]) is False


def test_store_reopens_existing_database(tmp_path, make_job):
    path = tmp_path / "nested" / "jobs.sqlite3"
    with JobStore(path) as first:
        first.add([make_job()])
    with JobStore(path) as second:
        assert len(second.rows()) == 1
        assert second.add([make_job()]) == []


def test_totals_on_empty_store(store):
    assert store.totals_by_user() == []


def test_schema_matches_the_job_dataclass(store):
    """The insert derives its columns from Job, so the table must agree with it."""
    from ipp_joblog.store import JOB_COLUMNS, SEEN_COLUMN

    # Order differs: ALTER TABLE appends, so migrated columns land after
    # first_seen_at. Inserts name their columns, so only the set matters.
    declared = {row["name"] for row in store._query("PRAGMA table_info(jobs)")}
    assert declared == {*JOB_COLUMNS, SEEN_COLUMN}


def test_a_new_database_is_stamped_with_the_current_version(store):
    from ipp_joblog.store import SCHEMA_VERSION

    assert store.version == SCHEMA_VERSION


def test_reopening_does_not_re_run_migrations(tmp_path, make_job):
    from ipp_joblog.store import SCHEMA_VERSION

    path = tmp_path / "jobs.sqlite3"
    with JobStore(path) as first:
        first.add([make_job()])
    with JobStore(path) as second:
        assert second.version == SCHEMA_VERSION
        assert len(second.rows()) == 1  # migration did not wipe anything


def test_a_pre_migration_database_is_adopted(tmp_path, make_job):
    """Databases written before user_version was stamped must upgrade in place."""
    import sqlite3

    from ipp_joblog.store import CREATE_JOBS, SCHEMA_VERSION

    path = tmp_path / "old.sqlite3"
    legacy = sqlite3.connect(path)
    legacy.execute(CREATE_JOBS)
    legacy.execute(
        "INSERT INTO jobs (key, job_id, job_name, user_name, state, impressions, sheets,"
        " color_mode, sides, document_format, created_at, completed_at, first_seen_at)"
        " VALUES ('old', 1, 'n', 'alice', 'completed', 5, 3, 'color', '', '', NULL, NULL, '')"
    )
    legacy.commit()
    legacy.close()

    with JobStore(path) as store:
        assert store.version == SCHEMA_VERSION
        assert len(store.rows()) == 1  # the existing row survived
        assert store.totals_by_user()[0].impressions == 5
        store.add([make_job()])  # and the table still accepts writes
        assert len(store.rows()) == 2


def test_a_database_from_the_future_is_refused(tmp_path):
    import sqlite3

    from ipp_joblog.store import SCHEMA_VERSION, StoreVersionError

    path = tmp_path / "future.sqlite3"
    ahead = sqlite3.connect(path)
    ahead.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    ahead.commit()
    ahead.close()

    with pytest.raises(StoreVersionError, match="newer ipp-joblog"):
        JobStore(path)


def test_unreported_sheets_stay_unknown(store, make_job):
    """A printer that never reports sheets must not look like it used no paper."""
    store.add([make_job(key="a", impressions=4, sheets=None)])
    total = store.totals_by_user()[0]
    assert total.impressions == 4
    assert total.sheets is None


def test_sheets_are_reported_only_when_every_job_reported_them(store, make_job):
    store.add(
        [
            make_job(key="known", impressions=4, sheets=2),
            make_job(key="unknown", impressions=4, sheets=None),
        ]
    )
    assert store.totals_by_user()[0].sheets is None

    with_all_known = [make_job(key="a", sheets=2), make_job(key="b", sheets=3)]
    fresh = JobStore(store.path.parent / "other.sqlite3")
    fresh.add(with_all_known)
    assert fresh.totals_by_user()[0].sheets == 5
    fresh.close()


def test_a_cancelled_job_really_did_use_no_sheets(store, make_job):
    """Zero impressions and zero sheets is a fact, not a silent printer."""
    store.add([make_job(key="cancelled", state="canceled", impressions=0, sheets=0)])
    assert store.totals_by_user()[0].sheets == 0


def test_migration_recovers_sheets_that_were_never_reported(tmp_path):
    """Rows stored as 0 sheets against real pages were a silent printer."""
    import sqlite3

    from ipp_joblog.store import CREATE_JOBS

    path = tmp_path / "legacy.sqlite3"
    legacy = sqlite3.connect(path)
    legacy.execute(CREATE_JOBS)
    for key, impressions, sheets in (("printed", 4, 0), ("cancelled", 0, 0), ("normal", 4, 2)):
        legacy.execute(
            "INSERT INTO jobs (key, job_id, job_name, user_name, state, impressions, sheets,"
            " color_mode, sides, document_format, created_at, completed_at, first_seen_at)"
            " VALUES (?, 1, 'n', 'alice', 'completed', ?, ?, '', '', '', NULL, NULL, '')",
            (key, impressions, sheets),
        )
    legacy.commit()
    legacy.close()

    with JobStore(path) as store:
        stored = {row["key"]: row["sheets"] for row in store.rows()}
    assert stored["printed"] is None  # pages but no sheets: the printer stayed silent
    assert stored["cancelled"] == 0  # nothing printed, so zero is the truth
    assert stored["normal"] == 2
