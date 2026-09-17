"""The dashboard, as one self-contained HTML page.

No templates, no assets, no JavaScript. A :class:`Dashboard` holds everything
the page shows; ``render`` turns it into a string that any static file server
can hand out unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from html import escape

from ipp_joblog.jobs import MONOCHROME_MODES
from ipp_joblog.store import PrinterSummary, UserTotals, add_sheets

STYLE = """
:root { color-scheme: light dark; --fg: #16181d; --dim: #5c6370; --bg: #fbfbfc;
        --card: #fff; --line: #e3e5ea; --bar: #3b73d4; --bar-mono: #9aa2b1; }
@media (prefers-color-scheme: dark) {
  :root { --fg: #e6e8ec; --dim: #99a0ad; --bg: #15171c; --card: #1d2027;
          --line: #2c303a; --bar: #5d8ee8; --bar-mono: #6b7385; }
}
* { box-sizing: border-box; }
body { margin: 0; padding: 2rem 1.25rem 4rem; background: var(--bg); color: var(--fg);
       font: 15px/1.5 ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width: 62rem; margin: 0 auto; }
h1 { font-size: 1.5rem; margin: 0 0 .25rem; }
h2 { font-size: 1rem; margin: 2.5rem 0 .75rem; font-weight: 600; }
.sub { color: var(--dim); margin: 0 0 2rem; font-size: .875rem; }
.cards { display: flex; flex-wrap: wrap; gap: .75rem; }
.card { flex: 1 1 8rem; background: var(--card); border: 1px solid var(--line);
        border-radius: .6rem; padding: .85rem 1rem; }
.card b { display: block; font-size: 1.6rem; font-weight: 600; font-variant-numeric: tabular-nums; }
.card span { color: var(--dim); font-size: .8rem; }
table { width: 100%; border-collapse: collapse; background: var(--card);
        border: 1px solid var(--line); border-radius: .6rem; overflow: hidden; }
th, td { padding: .55rem .85rem; text-align: right; border-bottom: 1px solid var(--line);
         font-variant-numeric: tabular-nums; white-space: nowrap; }
th { font-weight: 600; font-size: .8rem; color: var(--dim); text-align: right; }
th:first-child, td:first-child { text-align: left; }
td.name { white-space: normal; word-break: break-word; }
tr:last-child td { border-bottom: 0; }
.bar { height: .45rem; border-radius: .25rem; background: var(--bar); min-width: 2px; }
.bar.mono { background: var(--bar-mono); }
.bars { display: flex; gap: 2px; }
td.plot { width: 34%; }
.wrap { overflow-x: auto; }
footer { color: var(--dim); font-size: .8rem; margin-top: 2.5rem; }

@media (max-width: 48rem) {
  body { padding: 1.25rem .75rem 3rem; font-size: 14px; }
  h1 { font-size: 1.25rem; }
  h2 { margin-top: 1.75rem; }
  .sub { margin-bottom: 1.25rem; }
  .cards { gap: .5rem; }
  .card { flex: 1 1 6rem; padding: .6rem .75rem; }
  .card b { font-size: 1.25rem; }
  .facts, .grid { grid-template-columns: 1fr; }
  th, td { padding: .45rem .55rem; }
  /* The bars need width a phone does not have; the numbers say the same. */
  .plot { display: none; }

  /* Seven columns cannot fit, and squeezing the file name into a sliver is
     the worst of them. Each job becomes a card: its name on one line, the
     rest as a sentence under it. */
  #jobs, #jobs thead, #jobs tbody, #jobs tr, #jobs td { display: block; }
  #jobs thead { display: none; }
  #jobs tr { display: flex; flex-wrap: wrap; align-items: baseline;
             gap: .1rem .45rem; padding: .6rem .85rem;
             border-bottom: 1px solid var(--line); }
  #jobs tr:last-child { border-bottom: 0; }
  #jobs td { padding: 0; border: 0; text-align: left; white-space: nowrap; }
  #jobs td.name { order: -1; flex: 1 0 100%; white-space: normal;
                  font-weight: 600; margin-bottom: .1rem; }
  #jobs td:not(.name) { color: var(--dim); font-size: .85rem; }
  #jobs td.who::before, #jobs td.pages::before, #jobs td.sheets::before,
  #jobs td.ink::before, #jobs td.state::before { content: "·"; margin-right: .45rem; }
  #jobs td.pages::after { content: " pages"; }
  #jobs td.sheets::after { content: " sheets"; }
}
a { color: inherit; }
.card:hover { border-color: var(--bar); }
/* The whole card opens the printer's page: its title's link is stretched over
   the card. The web UI link is lifted above that overlay so it still works. */
.grid .card { position: relative; }
.grid .card b a { text-decoration: none; }
.grid .card b a::after { content: ""; position: absolute; inset: 0; }
.grid .card:hover b a { text-decoration: underline; }
.grid .card .ui { position: relative; z-index: 1; margin-top: .5rem;
                  font-size: .8rem; display: inline-block; }
.grid .card .ui a { color: var(--bar); }
.facts { display: grid; gap: 0 2rem; margin: 0;
         grid-template-columns: repeat(auto-fit, minmax(19rem, 1fr)); }
.facts div { min-width: 0; padding: .45rem 0; border-bottom: 1px solid var(--line); }
.facts dt { color: var(--dim); font-size: .8rem; white-space: nowrap;
            overflow: hidden; text-overflow: ellipsis; }
.facts dd { margin: .1rem 0 0; overflow-wrap: anywhere; }
.grid { display: grid; gap: .75rem; grid-template-columns: repeat(auto-fill, minmax(17rem, 1fr)); }
.grid .card b { font-size: 1.05rem; }
.grid .card em { font-style: normal; color: var(--dim); font-size: .8rem;
                 display: block; margin: .15rem 0 .5rem; }
.grid .card span { display: block; }
"""

# Only these may be turned into a clickable link. A printer supplies its own
# printer-more-info, so a hostile one must not be able to hand us a javascript:
# or data: URI to put in an href.
LINK_SCHEMES = ("http://", "https://")

# Facts rendered as clickable links rather than text.
LINK_FACTS = ("printer-more-info", "printer-more-info-manufacturer")

FACT_LABELS = {
    "host": "Host",
    "endpoint": "IPP endpoint",
    "printer-make-and-model": "Model",
    "printer-name": "Name",
    "printer-info": "Description",
    "printer-location": "Location",
    "printer-dns-sd-name": "Bonjour name",
    "printer-firmware-string-version": "Firmware",
    "printer-state-reasons": "State",
    "printer-more-info": "Reported info page",
    "printer-more-info-manufacturer": "Manufacturer page",
    "ipp-versions-supported": "IPP versions",
    "which-jobs-supported": "Job queues",
    "printer-up-time": "Uptime",
}

USER_HEADINGS = ("User", "Jobs", "Pages", "Sheets", "Color", "Mono", "")
JOB_HEADINGS = ("Completed", "User", "Pages", "Sheets", "", "State", "Name")


class Cell(str):
    """HTML that is already escaped and may be placed in a table as-is."""


def text(value: object) -> Cell:
    """Escape any value for display; nothing reported becomes a dash."""
    return Cell(escape(str(value)) if value not in (None, "") else "—")


def bars(color: int, mono: int, largest: int) -> Cell:
    """Two proportional bars, colour then mono, scaled against the busiest user."""
    scale = largest or 1
    widths = (("bar", color / scale * 100), ("bar mono", mono / scale * 100))
    segments = "".join(
        f'<div class="{name}" style="width:{width:.1f}%"></div>' for name, width in widths
    )
    return Cell(f'<div class="bars">{segments}</div>')


JOB_COLUMNS = ("when", "who", "pages", "sheets", "ink", "state", "name")
USER_COLUMNS = ("who", "", "", "", "", "", "plot")


def table(
    headings: tuple[str, ...],
    rows: list[list[Cell]],
    classes: tuple[str, ...] = (),
    css_id: str = "",
) -> str:
    """One HTML table. ``classes`` optionally names a CSS class per column."""

    def cell(index: int, value: Cell) -> str:
        css = classes[index] if index < len(classes) and classes[index] else ""
        return f'<td class="{css}">{value}</td>' if css else f"<td>{value}</td>"

    def head_cell(index: int, heading: str) -> str:
        css = classes[index] if index < len(classes) and classes[index] else ""
        return f'<th class="{css}">{heading}</th>' if css else f"<th>{heading}</th>"

    head = "".join(head_cell(index, heading) for index, heading in enumerate(headings))
    body = "\n".join(
        "<tr>" + "".join(cell(index, value) for index, value in enumerate(row)) + "</tr>"
        for row in rows
    )
    ident = f' id="{css_id}"' if css_id else ""
    return (
        f'<div class="wrap"><table{ident}>\n<thead><tr>{head}</tr></thead>\n'
        f"<tbody>\n{body}\n</tbody></table></div>"
    )


def _when(value: str | None) -> Cell:
    """A stored timestamp in the reader's own time zone.

    Printers report in whatever zone they hold: a Brother MFC-L3770CDW answers
    in UTC while an HP M880 answers in local time, so two printers would
    otherwise disagree with each other on one page.
    """
    if not value:
        return Cell("—")
    try:
        return Cell(datetime.fromisoformat(value).astimezone().strftime("%d %b %H:%M"))
    except ValueError:
        return text(value)


def duration(seconds: str) -> str:
    """Seconds of uptime as something a person reads, e.g. ``94 days, 1 hour``."""
    try:
        remaining = int(seconds)
    except ValueError:
        return seconds
    parts = []
    for size, unit in ((86400, "day"), (3600, "hour"), (60, "minute")):
        count, remaining = divmod(remaining, size)
        if count:
            parts.append(f"{count} {unit}{'s' if count != 1 else ''}")
    return ", ".join(parts[:2]) or f"{remaining} seconds"


def away(href: str, text: str) -> str:
    """A link that leaves the dashboard: the printer, or the project.

    Opened in a new tab, because the dashboard reloads itself every poll and a
    reader who followed a link in place would lose it. ``noopener`` keeps the
    opened page from reaching back through ``window.opener``.
    """
    return (
        f'<a href="{escape(href, quote=True)}" target="_blank" '
        f'rel="noopener noreferrer">{escape(text)}</a>'
    )


def link_text(url: str, host: str) -> str:
    """What to show for a link. A URL on the printer's own host shows its path.

    The host has its own row directly above, so repeating it only makes a
    string long enough to wrap in the middle of a word.
    """
    body = url.split("://", 1)[-1]
    if host and body.startswith(host):
        return body[len(host) :] or "/"
    return body


def link_of(facts: dict[str, str], name: str) -> str:
    """A device-supplied URI, but only if it is one we are willing to link to."""
    value = facts.get(name, "")
    return value if value.startswith(LINK_SCHEMES) else ""


def management_url(facts: dict[str, str]) -> tuple[str, bool]:
    """The printer's management interface, and whether we are inferring it.

    Always the root of its host. ``printer-more-info`` is not a substitute: a
    Brother MFC-L3770CDW points it at ``/net/net/airprint.html``, a page about
    AirPrint rather than the admin interface. What that attribute does prove,
    when it points at the same host, is that the host serves a web interface at
    all -- so the root stops being a guess and becomes an inference from
    evidence.
    """
    host = facts.get("host", "")
    if not host:
        return "", True
    url = f"http://{host}/"
    reported = link_of(facts, "printer-more-info")
    corroborated = reported.startswith(f"http://{host}/") or reported.startswith(f"https://{host}/")
    return url, not corroborated


def _row(label: str, value: str, *, href: str = "", suffix: str = "") -> str:
    # Every linked fact points at the printer or its maker, never back at us.
    shown = away(href, value) if href else escape(value)
    return f"<div><dt>{escape(label)}</dt><dd>{shown}{suffix}</dd></div>"


def facts_list(facts: dict[str, str]) -> str:
    """The printer's self-description, in the order FACT_LABELS declares."""
    web_ui, guessed = management_url(facts)
    rows, seen = [], {web_ui}
    for name, label in FACT_LABELS.items():
        value = facts.get(name, "")
        if name in LINK_FACTS:
            url = link_of(facts, name)
            # The reported link is worth showing only where it adds something.
            if url and url not in seen:
                seen.add(url)
                rows.append(_row(label, link_text(url, facts.get("host", "")), href=url))
        elif value and value not in seen:
            # Printers repeat themselves: a Brother gives the same model string
            # as its name, description and Bonjour name. Say it once.
            seen.add(value)
            rows.append(_row(label, duration(value) if name == "printer-up-time" else value))
        if name == "host" and web_ui:
            rows.append(_row("Web UI", web_ui, href=web_ui, suffix=" (guessed)" if guessed else ""))
    return f'<dl class="facts">{"".join(rows)}</dl>' if rows else ""


@dataclass(frozen=True, slots=True)
class Dashboard:
    """Everything the page shows, and nothing about how it is delivered."""

    printer: str
    generated_at: datetime
    totals: list[UserTotals] = field(default_factory=list)
    recent_jobs: list[dict] = field(default_factory=list)
    refresh_seconds: int = 0
    window: str = "all time"
    facts: dict[str, str] = field(default_factory=dict)

    @property
    def pages(self) -> int:
        return sum(total.impressions for total in self.totals)

    @property
    def sheets(self) -> int | None:
        return add_sheets(total.sheets for total in self.totals)

    @property
    def job_count(self) -> int:
        return sum(total.jobs for total in self.totals)

    @property
    def busiest(self) -> int:
        return max((total.impressions for total in self.totals), default=0)

    def user_rows(self) -> list[list[Cell]]:
        return [
            [
                text(total.user_name),
                text(total.jobs),
                text(total.impressions),
                text(total.sheets),
                text(total.color_impressions),
                text(total.mono_impressions),
                bars(total.color_impressions, total.mono_impressions, self.busiest),
            ]
            for total in self.totals
        ]

    def job_rows(self) -> list[list[Cell]]:
        return [
            [
                _when(job["completed_at"]),
                text(job["user_name"]),
                text(job["impressions"]),
                text(job["sheets"]),
                text("mono" if job["color_mode"] in MONOCHROME_MODES else "color"),
                text(job["state"]),
                text(job["job_name"]),
            ]
            for job in self.recent_jobs
        ]

    def cards(self) -> str:
        counts = (
            (self.pages, "pages"),
            (self.sheets, "sheets"),
            (self.job_count, "jobs"),
            (len(self.totals), "users"),
        )
        return "".join(
            f'<div class="card"><b>{text(value)}</b><span>{label}</span></div>'
            for value, label in counts
        )

    def render(self) -> str:
        """The whole page. ``refresh_seconds`` of 0 disables auto-reload."""
        refresh = (
            f'<meta http-equiv="refresh" content="{self.refresh_seconds}">'
            if self.refresh_seconds
            else ""
        )
        empty = (
            '<p class="sub">No jobs recorded yet. The first poll may take a moment.</p>'
            if not self.totals
            else ""
        )
        return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="format-detection" content="telephone=no">
{refresh}<title>Printer usage — {escape(self.printer)}</title>
<style>{STYLE}</style></head>
<body><main>
<h1>Printer usage</h1>
<p class="sub">{escape(self.printer)} · {escape(self.window)} ·
updated {self.generated_at.strftime("%d %b %Y %H:%M:%S")}</p>
{empty}
<div class="cards">{self.cards()}</div>

<h2>Per user</h2>
{table(USER_HEADINGS, self.user_rows(), classes=USER_COLUMNS, css_id="users")}

<h2>Recent jobs</h2>
{table(JOB_HEADINGS, self.job_rows(), classes=JOB_COLUMNS, css_id="jobs")}

<h2>About this printer</h2>
{facts_list(self.facts) or '<p class="sub">Nothing recorded yet.</p>'}

<footer><a href="index.html">All printers</a> · generated by
{away("https://github.com/schlomo/ipp-joblog", "ipp-joblog")}
— read straight from the printer over IPP.</footer>
</main></body></html>
"""


@dataclass(frozen=True, slots=True)
class Overview:
    """The index: one card per printer found in the state directory."""

    printers: list[PrinterSummary]
    generated_at: datetime
    refresh_seconds: int = 0

    def cards(self) -> str:
        if not self.printers:
            return '<p class="sub">No printers yet. Poll one and it will appear here.</p>'
        return '<div class="grid">' + "".join(self.card(p) for p in self.printers) + "</div>"

    @staticmethod
    def card(printer: PrinterSummary) -> str:
        """One printer: its usage, linking to its page and to the printer itself."""
        web_ui, _ = management_url(printer.facts)
        # Two destinations, so the card cannot be one big link.
        admin = f'<span class="ui">{away(web_ui, "Printer web UI ↗")}</span>' if web_ui else ""
        return (
            '<div class="card">'
            f'<b><a href="{escape(printer.page, quote=True)}">{escape(printer.host)}</a></b>'
            f"<em>{escape(printer.model)}</em>"
            f"<span>{printer.pages} pages · {text(printer.sheets)} sheets</span>"
            f"<span>{printer.jobs} jobs · {printer.users} users</span>"
            f"<span>last job {_when(printer.last_job_at)}</span>"
            f"{admin}"
            "</div>"
        )

    def render(self) -> str:
        refresh = (
            f'<meta http-equiv="refresh" content="{self.refresh_seconds}">'
            if self.refresh_seconds
            else ""
        )
        total = sum(printer.pages for printer in self.printers)
        return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="format-detection" content="telephone=no">
{refresh}<title>Printers</title>
<style>{STYLE}</style></head>
<body><main>
<h1>Printers</h1>
<p class="sub">{len(self.printers)} printer(s) · {total} pages ·
updated {self.generated_at.strftime("%d %b %Y %H:%M:%S")}</p>
{self.cards()}
<footer>Generated by {away("https://github.com/schlomo/ipp-joblog", "ipp-joblog")}
— read straight from the printers over IPP.</footer>
</main></body></html>
"""
