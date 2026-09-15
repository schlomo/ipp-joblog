from __future__ import annotations

from datetime import UTC, datetime

from ipp_joblog.page import Dashboard
from ipp_joblog.store import UserTotals

TOTALS = [
    UserTotals("alice", jobs=9, impressions=250, sheets=125, color_impressions=176),
    UserTotals("bob", jobs=10, impressions=28, sheets=17, color_impressions=9),
]
JOB = {
    "completed_at": "2026-09-10T22:39:30+01:00",
    "user_name": "alice",
    "impressions": 2,
    "sheets": 1,
    "color_mode": "monochrome",
    "state": "completed",
    "job_name": "Invoice.pdf",
}


def dashboard(totals=TOTALS, jobs=(JOB,), **kwargs) -> Dashboard:
    return Dashboard(
        printer="printer.example",
        generated_at=datetime(2026, 9, 10, 22, 40),
        totals=list(totals),
        recent_jobs=list(jobs),
        **kwargs,
    )


def test_page_is_self_contained():
    html = dashboard().render()
    assert html.startswith("<!doctype html>")
    assert "<style>" in html
    assert "<script" not in html
    assert "src=" not in html  # no external assets to fetch


def test_totals_are_summed_across_users():
    board = dashboard()
    assert board.pages == 278
    assert board.sheets == 142
    assert board.job_count == 19
    assert board.busiest == 250
    assert ">278<" in board.render()


def test_page_escapes_user_and_job_names():
    hostile = dict(JOB, job_name="<script>alert(1)</script>", user_name="a&b")
    html = dashboard(totals=[UserTotals("<img src=x>", 1, 1, 1, 0)], jobs=[hostile]).render()
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
    assert "<img src=x>" not in html
    assert "a&amp;b" in html


def test_refresh_is_opt_in():
    assert 'http-equiv="refresh"' not in dashboard().render()
    assert 'content="60"' in dashboard(refresh_seconds=60).render()


def test_empty_store_renders_a_friendly_page():
    board = dashboard(totals=[], jobs=[])
    html = board.render()
    assert "No jobs recorded yet" in html
    assert board.pages == 0
    assert ">0<" in html


def test_bars_are_scaled_to_the_busiest_user():
    assert "width:70.4%" in dashboard().render()  # alice's 176 colour pages of 250


def test_missing_values_render_as_a_dash():
    blank = dict(JOB, user_name="", job_name="", completed_at=None)
    assert dashboard(jobs=[blank]).render().count("—") >= 3


def test_unreported_sheets_show_as_a_dash_not_a_zero():
    """A Brother reports no sheet counts; the page must not claim zero paper."""
    totals = [UserTotals("alice", jobs=1, impressions=4, sheets=None, color_impressions=0)]
    job = dict(JOB, sheets=None)
    html = dashboard(totals=totals, jobs=[job]).render()
    assert "—" in html
    assert ">0<" not in html.split("Per user")[0]  # no fake zero in the summary cards


def test_sheets_still_shown_when_the_printer_reports_them():
    assert ">142<" in dashboard().render()


def test_the_web_ui_is_the_host_root_not_whatever_was_reported():
    """A Brother points printer-more-info at an AirPrint page, not its admin UI.

    Reporting it does prove the host serves a web interface, so the root stops
    being a guess -- but the root is still what gets offered as the Web UI.
    """
    from ipp_joblog.page import management_url

    facts = {
        "host": "brw0011223344ff.local.",
        "printer-more-info": "http://brw0011223344ff.local./net/net/airprint.html",
    }
    assert management_url(facts) == ("http://brw0011223344ff.local./", False)

    html = dashboard(facts=facts).render()
    assert 'href="http://brw0011223344ff.local./"' in html
    assert "(guessed)" not in html
    # the reported page is still offered, separately and honestly labelled
    assert 'href="http://brw0011223344ff.local./net/net/airprint.html"' in html
    assert "Reported info page" in html


def test_a_reported_page_on_another_host_does_not_vouch_for_the_root():
    from ipp_joblog.page import management_url

    facts = {"host": "printer", "printer-more-info": "http://support.example/help"}
    assert management_url(facts) == ("http://printer/", True)
    assert "(guessed)" in dashboard(facts=facts).render()


def test_the_web_ui_link_is_guessed_from_the_host_when_not_reported():
    html = dashboard(facts={"host": "brw0011223344ff.local."}).render()
    assert 'href="http://brw0011223344ff.local./"' in html
    assert "(guessed)" in html


def test_a_printer_cannot_inject_a_script_url():
    """printer-more-info comes from the device, so it may not become any href."""
    from ipp_joblog.page import management_url

    hostile = {"host": "printer", "printer-more-info": "javascript:alert(1)"}
    assert management_url(hostile) == ("http://printer/", True)
    assert "javascript:" not in dashboard(facts=hostile).render()


def test_no_web_ui_link_without_a_host():
    from ipp_joblog.page import management_url

    assert management_url({}) == ("", True)
    assert "Web UI" not in dashboard(facts={}).render()


def test_repeated_values_are_said_once():
    """A Brother reports the same string as model, name, description and Bonjour name."""
    model = "Brother MFC-L3770CDW series"
    html = dashboard(
        facts={
            "host": "brw.local.",
            "printer-make-and-model": model,
            "printer-name": model,
            "printer-info": model,
            "printer-dns-sd-name": model,
        }
    ).render()
    assert html.count(model) == 1


def test_uptime_is_shown_as_a_duration_not_a_digit_string():
    html = dashboard(facts={"host": "p", "printer-up-time": "8126224"}).render()
    assert "94 days, 1 hour" in html
    assert "8126224" not in html
    # long digit strings get auto-linked as phone numbers without this
    assert 'name="format-detection" content="telephone=no"' in html


def test_times_are_shown_in_the_readers_zone_not_the_printers():
    """A Brother answers in UTC; an M880 in local time. One page, one clock."""

    from ipp_joblog.page import _when

    utc_job = dict(JOB, completed_at="2026-09-15T14:21:21+00:00")
    expected = datetime(2026, 9, 15, 14, 21, 21, tzinfo=UTC).astimezone()
    assert _when(utc_job["completed_at"]) == expected.strftime("%d %b %H:%M")
    assert expected.strftime("%H:%M") in dashboard(jobs=[utc_job]).render()


def test_an_unparseable_timestamp_is_shown_as_is():
    from ipp_joblog.page import _when

    assert _when("not a time") == "not a time"


def test_a_link_on_the_printers_own_host_shows_only_its_path():
    """Repeating the host made the value long enough to break mid-word."""
    from ipp_joblog.page import link_text

    host = "brw0011223344ff.local."
    assert link_text(f"http://{host}/net/net/airprint.html", host) == "/net/net/airprint.html"
    assert link_text("http://support.brother.com/help", host) == "support.brother.com/help"

    html = dashboard(
        facts={"host": host, "printer-more-info": f"http://{host}/net/net/airprint.html"}
    ).render()
    assert ">/net/net/airprint.html<" in html  # short text...
    assert f'href="http://{host}/net/net/airprint.html"' in html  # ...full target


def test_pages_are_built_for_small_screens():
    html = dashboard().render()
    assert 'name="viewport" content="width=device-width, initial-scale=1"' in html
    assert "@media (max-width: 48rem)" in html
    assert ".facts, .grid { grid-template-columns: 1fr; }" in html


def test_wide_tables_scroll_inside_themselves():
    """The page must never scroll sideways, however many columns a table has."""
    html = dashboard().render()
    assert html.count('<div class="wrap">') == 2
    assert ".wrap { overflow-x: auto; }" in html


def test_the_bar_column_can_be_hidden_as_a_unit():
    """Header and cells share the class, so the phone rule removes both."""
    html = dashboard().render()
    assert '<th class="plot">' in html
    assert '<td class="plot">' in html


def test_links_that_leave_the_dashboard_open_a_new_tab():
    """The page reloads itself every poll, so following a link in place loses it."""
    html = dashboard(facts={"host": "hpm880"}).render()
    web_ui = next(line for line in html.splitlines() if "http://hpm880/" in line)
    assert 'target="_blank"' in web_ui
    assert 'rel="noopener noreferrer"' in web_ui


def test_links_within_the_dashboard_stay_in_the_tab():
    html = dashboard().render()
    back = next(line for line in html.splitlines() if 'href="index.html"' in line)
    assert "target=" not in back
