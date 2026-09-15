"""Two pollers, one state directory: what must not break."""

from __future__ import annotations

import multiprocessing
import threading
from pathlib import Path

from ipp_joblog.serve import summarise_all, write_index
from ipp_joblog.store import JobStore


def _seed(state_dir: Path, host: str, make_job, count: int) -> None:
    with JobStore(state_dir / f"{host}.sqlite3") as store:
        store.remember_facts({"host": host})
        store.add([make_job(key=f"{host}-{n}", impressions=1) for n in range(count)])


def test_databases_use_wal_so_readers_do_not_wait(tmp_path):
    with JobStore(tmp_path / "hpm880.sqlite3") as store:
        mode = store._query("PRAGMA journal_mode")[0]["journal_mode"]
    assert mode == "wal"


def test_one_printer_can_be_read_while_another_is_written(tmp_path, make_job):
    """The overview opens every database, including ones being polled."""
    _seed(tmp_path, "hpm880", make_job, 3)
    with JobStore(tmp_path / "brother.sqlite3") as writing:
        writing.add([make_job(key="b1", impressions=5)])
        summaries = summarise_all(tmp_path)  # peer still open with data written
    assert {summary.slug for summary in summaries} == {"hpm880", "brother"}
    assert sum(summary.pages for summary in summaries) == 8


def test_two_writers_on_one_database_do_not_duplicate(tmp_path, make_job):
    """INSERT OR IGNORE means a second poller on the same printer is harmless."""
    path = tmp_path / "hpm880.sqlite3"
    job = make_job(key="shared", impressions=7)
    with JobStore(path) as first, JobStore(path) as second:
        assert first.add([job]) == [job]
        assert second.add([job]) == []
    with JobStore(path) as store:
        assert len(store.rows()) == 1
        assert store.totals_by_user()[0].impressions == 7


def _write_index_repeatedly(state_dir: str, public: str, rounds: int) -> None:
    for _ in range(rounds):
        write_index(Path(state_dir), Path(public), refresh_seconds=0)


def test_concurrent_index_writers_never_publish_a_torn_page(tmp_path, make_job):
    """Every serve process rewrites index.html; the file must stay whole."""
    _seed(tmp_path, "hpm880", make_job, 40)
    _seed(tmp_path, "brother", make_job, 40)
    public = tmp_path / "public"

    processes = [
        multiprocessing.Process(
            target=_write_index_repeatedly, args=(str(tmp_path), str(public), 15)
        )
        for _ in range(4)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=60)
        assert process.exitcode == 0

    html = (public / "index.html").read_text()
    assert html.startswith("<!doctype html>")
    assert html.rstrip().endswith("</html>")
    assert html.count("<!doctype html>") == 1  # not two pages spliced together
    assert 'href="hpm880.html"' in html
    assert not list(public.glob(".*tmp")), "temporary files left behind"


def test_threads_writing_the_same_page_leave_it_whole(tmp_path, make_job):
    _seed(tmp_path, "hpm880", make_job, 20)
    public = tmp_path / "public"
    threads = [
        threading.Thread(target=_write_index_repeatedly, args=(str(tmp_path), str(public), 20))
        for _ in range(4)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    html = (public / "index.html").read_text()
    assert html.count("<!doctype html>") == 1
    assert html.rstrip().endswith("</html>")
