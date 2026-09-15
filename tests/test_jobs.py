from __future__ import annotations

from datetime import datetime

from ipp_joblog.jobs import Job, job_key


def test_jobs_decode_from_recorded_response(job_groups):
    jobs = [Job.from_attributes(group) for group in job_groups]
    assert len(jobs) == 20
    assert {job.user_name for job in jobs} == {"alice", "bob", "SM-A146P"}


def test_impressions_include_copies(job_groups):
    """A 12-page document printed 6 times is charged as 72 impressions."""
    jobs = {job.job_id: job for job in map(Job.from_attributes, job_groups)}
    assert jobs[19].impressions == 72
    assert jobs[19].sheets == 36  # duplex: half as many sheets as impressions


def test_key_is_stable_and_unique(job_groups):
    keys = [Job.from_attributes(group).key for group in job_groups]
    assert len(set(keys)) == len(keys)
    assert keys == [Job.from_attributes(group).key for group in job_groups]


def test_key_pairs_the_job_id_with_a_clock(job_groups):
    """job-id restarts at 1 after a reboot, so a clock must be part of the key."""
    group = job_groups[0]
    first = Job.from_attributes(group)
    assert str(first.job_id) in first.key
    assert str(group["time-at-creation"].value) in first.key


def test_cancelled_jobs_are_kept_with_their_real_page_count(job_groups):
    jobs = [Job.from_attributes(group) for group in job_groups]
    unfinished = [job for job in jobs if job.state in ("canceled", "aborted")]
    assert unfinished, "fixture should contain cancelled jobs"
    assert any(job.impressions > 0 for job in unfinished), "aborted jobs still consume pages"


def test_color_flag_follows_print_color_mode(job_groups):
    jobs = {job.job_id: job for job in map(Job.from_attributes, job_groups)}
    assert jobs[19].is_color is False  # print-color-mode == monochrome
    assert jobs[2].is_color is True


def test_missing_attributes_do_not_raise():
    job = Job.from_attributes({})
    assert job.impressions == 0
    assert job.user_name == ""
    assert job.state == "unknown"
    assert job.key == "job-0"


def test_job_uuid_is_not_usable_as_an_identity(job_groups):
    """The printer leaves job-uuid zeroed except for Mopria clients."""
    uuids = [group["job-uuid"].value for group in job_groups]
    zeroed = [value for value in uuids if value.endswith("0" * 12)]
    assert len(zeroed) == len(uuids) - 1
    assert len(set(zeroed)) == 1


ZEROED_UUID = "urn:uuid:00000000-0000-0000-0000-000000000000"
REAL_UUID = "urn:uuid:70325eb7-4d94-4a11-bbc9-465a119f2ce8"


def test_job_key_prefers_a_real_uuid(attributes, cet):
    group = attributes(
        job_id=7,
        job_uuid=REAL_UUID,
        date_time_at_creation=datetime(2026, 9, 10, 20, 45, tzinfo=cet),
    )
    assert job_key(group) == REAL_UUID


def test_job_key_ignores_a_zeroed_uuid(attributes, cet):
    group = attributes(
        job_id=7,
        job_uuid=ZEROED_UUID,
        date_time_at_creation=datetime(2026, 9, 10, 20, 45, tzinfo=cet),
    )
    assert job_key(group) == "2026-09-10T20:45:00+01:00#7"


def test_job_key_falls_back_to_uptime_without_a_wall_clock(attributes):
    """Printers that do not know the date still report seconds of uptime."""
    assert job_key(attributes(job_id=7, time_at_creation=375982)) == "uptime-375982#7"


def test_job_key_last_resort_is_the_job_id(attributes):
    assert job_key(attributes(job_id=7)) == "job-7"


def test_job_keys_stay_distinct_without_timestamps(attributes):
    """Without this, every job on such a printer would collapse to one key."""
    keys = {
        job_key(attributes(job_id=job_id, time_at_creation=100 + job_id)) for job_id in range(1, 6)
    }
    assert len(keys) == 5


def test_job_survives_a_printer_reporting_only_the_required_attributes(attributes):
    job = Job.from_attributes(
        attributes(job_id=3, job_originating_user_name="carol", job_impressions_completed=4)
    )
    assert job.user_name == "carol"
    assert job.impressions == 4
    assert job.sheets is None  # not reported is not the same as zero
    assert job.is_color is False
    assert job.key == "job-3"


def test_key_ignores_a_wall_clock_that_jitters(attributes, cet):
    """A Brother MFC-L3770CDW reports a creation time that moves between reads.

    Two observations of job 1330 came back a second apart while time-at-creation
    stayed at 8122284. Keying on the wall clock would store the job twice.
    """
    first = attributes(
        job_id=1330,
        time_at_creation=8122284,
        date_time_at_creation=datetime(2026, 9, 15, 13, 25, 5, tzinfo=cet),
    )
    second = attributes(
        job_id=1330,
        time_at_creation=8122284,
        date_time_at_creation=datetime(2026, 9, 15, 13, 25, 6, tzinfo=cet),
    )
    assert job_key(first) == job_key(second)


def test_a_jittering_printer_stores_each_job_once(store, attributes, cet):
    """The end-to-end consequence: two polls of one job must not double-count it."""
    observations = [
        Job.from_attributes(
            attributes(
                job_id=1330,
                time_at_creation=8122284,
                job_originating_user_name="schlomo",
                job_impressions_completed=1,
                date_time_at_creation=datetime(2026, 9, 15, 13, 25, second, tzinfo=cet),
            )
        )
        for second in (5, 6)
    ]
    store.add(observations[:1])
    store.add(observations[1:])
    assert len(store.rows()) == 1
    assert store.totals_by_user()[0].impressions == 1
