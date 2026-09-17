from __future__ import annotations

from urllib.request import urlopen

from ipp_joblog.serve import start_server, summarise_all, write_index, write_page
from ipp_joblog.store import JobStore


def publish(store, directory, printer="printer.example", refresh=0):
    return write_page(store, directory, printer=printer, refresh_seconds=refresh)


def test_the_page_is_named_after_the_database(tmp_path, make_job):
    with JobStore(tmp_path / "hpm880.sqlite3") as store:
        store.add([make_job()])
        target = publish(store, tmp_path / "public")
    assert target.name == "hpm880.html"
    assert "alice" in target.read_text()


def test_publishing_leaves_no_temporary_file_behind(tmp_path):
    directory = tmp_path / "public"
    with JobStore(tmp_path / "hpm880.sqlite3") as store:
        publish(store, directory)
    assert [path.name for path in directory.iterdir()] == ["hpm880.html"]


def test_republishing_replaces_the_previous_page(tmp_path, make_job):
    directory = tmp_path / "public"
    with JobStore(tmp_path / "hpm880.sqlite3") as store:
        publish(store, directory)
        assert "alice" not in (directory / "hpm880.html").read_text()
        store.add([make_job()])
        publish(store, directory)
    assert "alice" in (directory / "hpm880.html").read_text()


def test_the_page_shows_what_the_printer_said_about_itself(tmp_path):
    with JobStore(tmp_path / "hpm880.sqlite3") as store:
        store.remember_facts(
            {"host": "hpm880", "printer-make-and-model": "HP Color LaserJet flow MFP M880"}
        )
        target = publish(store, tmp_path / "public")
    html = target.read_text()
    assert "HP Color LaserJet flow MFP M880" in html
    assert "Model" in html  # the friendly label, not the IPP attribute name


def test_the_index_lists_every_printer(tmp_path, make_job):
    for host, pages in (("hpm880", 72), ("brother", 3)):
        with JobStore(tmp_path / f"{host}.sqlite3") as store:
            store.remember_facts({"host": host, "printer-make-and-model": f"{host} model"})
            store.add([make_job(key=host, impressions=pages)])

    target = write_index(tmp_path, tmp_path / "public", refresh_seconds=0)
    html = target.read_text()
    assert target.name == "index.html"
    assert 'href="hpm880.html"' in html
    assert 'href="brother.html"' in html
    assert "hpm880 model" in html
    assert html.index("hpm880.html") < html.index("brother.html")  # busiest first


def test_the_index_is_honest_when_there_are_no_printers(tmp_path):
    html = write_index(tmp_path, tmp_path / "public", refresh_seconds=0).read_text()
    assert "No printers yet" in html


def test_summaries_are_read_from_the_databases(tmp_path, make_job):
    with JobStore(tmp_path / "hpm880.sqlite3") as store:
        store.remember_facts({"host": "hpm880"})
        store.add([make_job(user_name="alice", impressions=72, sheets=36)])
    summary = summarise_all(tmp_path)[0]
    assert (summary.slug, summary.host, summary.pages, summary.sheets) == (
        "hpm880",
        "hpm880",
        72,
        36,
    )
    assert summary.page == "hpm880.html"
    assert summary.users == 1


def test_server_hands_out_both_pages(tmp_path, make_job):
    directory = tmp_path / "public"
    with JobStore(tmp_path / "hpm880.sqlite3") as store:
        store.add([make_job(user_name="alice", impressions=72)])
        publish(store, directory)
    write_index(tmp_path, directory, refresh_seconds=30)

    server = start_server(directory, port=0, host="127.0.0.1")
    try:
        port = server.server_address[1]
        index = urlopen(f"http://127.0.0.1:{port}/", timeout=5).read().decode()
        detail = urlopen(f"http://127.0.0.1:{port}/hpm880.html", timeout=5).read().decode()
        assert 'href="hpm880.html"' in index
        assert "alice" in detail
        assert ">72<" in detail
    finally:
        server.shutdown()
        server.server_close()


def test_server_creates_a_missing_directory(tmp_path):
    directory = tmp_path / "not-yet"
    server = start_server(directory, port=0, host="127.0.0.1")
    try:
        assert directory.is_dir()
    finally:
        server.shutdown()
        server.server_close()


def test_a_never_polled_database_says_so_rather_than_guessing(tmp_path, make_job):
    """A leftover database from an older layout should not read as a real printer."""
    with JobStore(tmp_path / "printer-jobs.sqlite3") as store:
        store.add([make_job()])  # jobs, but no facts were ever recorded
    summary = summarise_all(tmp_path)[0]
    assert summary.polled is False
    assert "never polled" in summary.model
    assert (
        "never polled" in write_index(tmp_path, tmp_path / "public", refresh_seconds=0).read_text()
    )


def test_overview_cards_link_to_the_page_and_to_the_printer(tmp_path, make_job):
    with JobStore(tmp_path / "hpm880.sqlite3") as store:
        store.remember_facts({"host": "hpm880", "printer-make-and-model": "HP M880"})
        store.add([make_job()])
    html = write_index(tmp_path, tmp_path / "public", refresh_seconds=0).read_text()
    assert 'href="hpm880.html"' in html  # its own page
    assert 'href="http://hpm880/"' in html  # the printer itself
    assert "Printer web UI" in html
    assert "<a" not in html.split('href="hpm880.html"')[1].split("</a>")[0]  # no nested links


def test_overview_omits_the_printer_link_when_the_host_is_unknown(tmp_path, make_job):
    with JobStore(tmp_path / "printer-jobs.sqlite3") as store:
        store.add([make_job()])
    html = write_index(tmp_path, tmp_path / "public", refresh_seconds=0).read_text()
    assert "Printer web UI" not in html


def test_the_whole_card_opens_the_printer_page(tmp_path, make_job):
    """Clicking anywhere on the card works, without nesting one link in another."""
    with JobStore(tmp_path / "hpm880.sqlite3") as store:
        store.remember_facts({"host": "hpm880"})
        store.add([make_job()])
    html = write_index(tmp_path, tmp_path / "public", refresh_seconds=0).read_text()
    assert ".grid .card b a::after" in html  # the title's link covers the card
    assert ".grid .card { position: relative; }" in html
    assert ".grid .card .ui { position: relative; z-index: 1;" in html  # sits above it
    card = html.split('<div class="card">')[1].split("</div>")[0]
    assert card.count("<a ") == 2  # page link and printer link
    # ...and they are siblings: the first closes before the second opens, so no
    # anchor is nested inside another.
    first_close, second_open = card.index("</a>"), card.index("<a ", card.index("<a ") + 1)
    assert first_close < second_open


def test_the_printer_link_on_a_card_opens_a_new_tab(tmp_path, make_job):
    """Its own page stays in the tab; the printer itself does not."""
    with JobStore(tmp_path / "hpm880.sqlite3") as store:
        store.remember_facts({"host": "hpm880"})
        store.add([make_job()])
    html = write_index(tmp_path, tmp_path / "public", refresh_seconds=0).read_text()
    card = html.split('<div class="card">')[1].split("</div>")[0]
    own_page, printer = card.split("</a>")[0], card.split("</a>")[1]
    assert "target=" not in own_page  # the card's own link navigates in place
    assert 'target="_blank"' in printer and 'rel="noopener noreferrer"' in printer


def test_the_overview_corrects_a_printers_last_job_time(tmp_path, make_job):
    """A misreporting printer's last-job time on the index is corrected too."""
    from datetime import datetime, timedelta, timezone

    with JobStore(tmp_path / "hpm880.sqlite3") as store:
        store.remember_facts({"host": "hpm880"})
        completed = datetime(2026, 9, 17, 8, 50, tzinfo=timezone(timedelta(hours=2)))
        store.add([make_job(completed_at=completed)], correction=timedelta(minutes=-60))
    summary = summarise_all(tmp_path)[0]
    # last_job_at is the corrected instant, an hour back from what was reported
    assert summary.last_job_at == "2026-09-17T07:50:00+02:00"


def test_the_jobs_csv_stays_the_raw_record(tmp_path, make_job):
    """Correction is a display concern; the CSV export is what the printer sent."""
    from datetime import datetime, timedelta, timezone

    from ipp_joblog.output import jobs_csv

    completed = datetime(2026, 9, 17, 6, 50, tzinfo=timezone(timedelta(hours=1)))
    with JobStore(tmp_path / "hpm880.sqlite3") as store:
        store.add([make_job(completed_at=completed)], correction=timedelta(minutes=-60))
        rows = store.rows()
        csv = jobs_csv(rows)
    # the printer's literal value is preserved in the raw column
    assert rows[0]["completed_at_raw"] == "2026-09-17T06:50:00+01:00"
    # and completed_at is the corrected instant
    assert rows[0]["completed_at"] == "2026-09-17T05:50:00+01:00"
    assert "completed_at_raw" in csv
