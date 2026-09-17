"""Correcting printers that misreport their clock.

Some printers -- an HP M880, a Brother MFC-L3770CDW -- show the right time on
their own front panel but report it over IPP with the wrong UTC offset, off by
the daylight-saving hour. Their timestamps land an hour in the future.

The offset is measured, never assumed: ``printer-current-time`` read against our
own clock is the printer's error directly, with no guesswork about time zones or
daylight saving. A printer whose clock is right measures ~0 and is left alone.
"""

from __future__ import annotations

from datetime import datetime, timedelta

# The correction we store, keyed in the printer facts so display code can read
# it without a live printer.
KEY = "clock-correction-seconds"

# Clock misconfigurations land on a time-zone step; genuine drift does not.
# Round the measured error to the nearest half hour, but only when it sits
# within a few minutes of one -- so a whole-hour daylight-saving mistake is
# corrected while ordinary clock drift is left as the noise it is.
_STEP = timedelta(minutes=30)
_GUARD = timedelta(minutes=5)


def correction_for(ahead: timedelta | None) -> timedelta:
    """The shift to apply, given how far a printer's clock leads ours.

    ``ahead`` is measured two ways -- ``printer-current-time`` against our clock,
    or a freshly finished job whose completion is in our future -- and read the
    same way here. Zero unless the printer is clearly a whole time-zone step out,
    so a small drift, or a clock that is right, is left alone.
    """
    if ahead is None:
        return timedelta()
    nearest = round(ahead / _STEP) * _STEP
    if abs(ahead - nearest) > _GUARD:
        return timedelta()  # not a time-zone-shaped error; leave it be
    return -nearest  # subtract the printer's lead to recover the true time


def stored_correction(facts: dict[str, str]) -> timedelta:
    """The correction recorded for a printer, or none."""
    raw = facts.get(KEY, "")
    return timedelta(seconds=int(raw)) if raw.lstrip("-").isdigit() else timedelta()


def as_fact(correction: timedelta) -> str:
    return str(int(correction.total_seconds()))


def apply(moment: datetime | None, correction: timedelta) -> datetime | None:
    return moment + correction if moment is not None else None


def apply_iso(stamp: str | None, correction: timedelta) -> str | None:
    """Shift a stored ISO timestamp, leaving anything unparseable untouched."""
    if not stamp or not correction:
        return stamp
    try:
        return (datetime.fromisoformat(stamp) + correction).isoformat()
    except ValueError:
        return stamp
