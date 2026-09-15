from __future__ import annotations

import pytest

from ipp_joblog.cli import main
from ipp_joblog.store import (
    DatabaseChoiceError,
    JobStore,
    database_name,
    databases,
    resolve_database,
)


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("hpm880", "hpm880.sqlite3"),
        ("brw0011223344ff.local.", "brw0011223344ff.local.sqlite3"),  # trailing dot dropped
        ("192.168.1.50", "192.168.1.50.sqlite3"),
        ("HPM880", "hpm880.sqlite3"),  # case folded, so one printer means one file
        ("HP M880 (office)", "hp-m880-office.sqlite3"),
        ("../../etc/passwd", "etc-passwd.sqlite3"),  # never escapes the state directory
    ],
)
def test_database_name_is_derived_from_the_host(host, expected):
    assert database_name(host) == expected


def test_two_printers_get_two_databases(tmp_path, make_job):
    for host in ("hpm880", "brw0011223344ff.local."):
        with JobStore(resolve_database(tmp_path, host)) as store:
            store.add([make_job(user_name=host)])

    assert [path.name for path in databases(tmp_path)] == [
        "brw0011223344ff.local.sqlite3",
        "hpm880.sqlite3",
    ]
    with JobStore(resolve_database(tmp_path, "hpm880")) as store:
        assert store.totals_by_user()[0].user_name == "hpm880"


def test_a_host_selects_its_own_database_even_before_it_exists(tmp_path):
    assert resolve_database(tmp_path, "hpm880").name == "hpm880.sqlite3"
    assert not resolve_database(tmp_path, "hpm880").exists()


def test_without_a_host_the_only_database_is_used(tmp_path, make_job):
    with JobStore(tmp_path / "hpm880.sqlite3") as store:
        store.add([make_job()])
    assert resolve_database(tmp_path, None).name == "hpm880.sqlite3"


def test_without_a_host_several_databases_are_ambiguous(tmp_path):
    for name in ("hpm880.sqlite3", "brother.sqlite3"):
        JobStore(tmp_path / name).close()
    with pytest.raises(DatabaseChoiceError, match="pass --host to choose"):
        resolve_database(tmp_path, None)


def test_without_a_host_and_no_databases_says_to_poll_first(tmp_path):
    with pytest.raises(DatabaseChoiceError, match="poll a printer first"):
        resolve_database(tmp_path, None)


def test_report_picks_the_single_printer_without_being_told(tmp_path, make_job, capsys):
    with JobStore(tmp_path / "hpm880.sqlite3") as store:
        store.add([make_job(user_name="alice", impressions=72)])
    capsys.readouterr()
    assert main(["--state-dir", str(tmp_path), "report"]) == 0
    assert "alice" in capsys.readouterr().out


def test_report_asks_which_printer_when_there_are_several(tmp_path, capsys):
    for name in ("hpm880.sqlite3", "brother.sqlite3"):
        JobStore(tmp_path / name).close()
    capsys.readouterr()
    assert main(["--state-dir", str(tmp_path), "report"]) == 1
    assert "pass --host to choose one" in capsys.readouterr().err


def test_the_dashboard_lives_in_the_state_dir_by_default(tmp_path):
    from ipp_joblog.cli import Settings

    settings = Settings(host="hpm880", state_dir=tmp_path, timeout=5.0, path=None)
    assert settings.page_dir == tmp_path / "public"
    assert settings.database == tmp_path / "hpm880.sqlite3"
