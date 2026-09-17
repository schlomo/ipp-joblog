"""Correcting printers that misreport their clock.

Some printers -- an HP M880, a Brother MFC-L3770CDW -- show the right time on
their own panel but stamp IPP timestamps with a stale UTC offset, an hour out.
Their timestamps land an hour in the future.

The offset is measured, never assumed: ``printer-current-time`` read against our
own clock is the error directly, with no guesswork about time zones or daylight
saving. It is applied when a job is stored, so each job keeps the correction
that was true when it printed -- which is what makes a daylight-saving change
sort itself out instead of retroactively skewing an archive.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from ipp_joblog.ipp import IppClient, IppError

# Clock misconfigurations land on a time-zone step; genuine drift does not.
# Round the measured error to the nearest half hour, but only when it sits
# within a few minutes of one, so a whole-hour daylight-saving mistake is
# corrected while ordinary drift is left as the noise it is.
_STEP = timedelta(minutes=30)
_GUARD = timedelta(minutes=5)


def measure(client: IppClient) -> timedelta | None:
    """How far a printer's clock leads ours, or ``None`` if it will not say.

    ``printer-current-time`` is the printer's own now; against ours it is the
    offset exactly, with none of the guesswork a job's age would bring.
    """
    try:
        attributes = client.printer_attributes()
    except (IppError, OSError):
        return None
    current = attributes.get("printer-current-time")
    if current is not None and isinstance(current.value, datetime):
        return current.value - datetime.now().astimezone()
    return None


def correction_for(ahead: timedelta | None) -> timedelta:
    """The shift to apply to a printer's timestamps, given how far its clock leads.

    Zero unless the printer is clearly a whole time-zone step out, so a small
    drift, or a clock that is right, is left alone.
    """
    if ahead is None:
        return timedelta()
    nearest = round(ahead / _STEP) * _STEP
    if abs(ahead - nearest) > _GUARD:
        return timedelta()  # not a time-zone-shaped error; leave it be
    return -nearest  # subtract the printer's lead to recover the true instant


def apply(moment: datetime | None, correction: timedelta) -> datetime | None:
    return moment + correction if moment is not None else None
