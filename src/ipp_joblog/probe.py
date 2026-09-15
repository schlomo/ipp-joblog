"""Deciding whether a given printer can be accounted for at all.

Gathering the facts and printing them are separate: :func:`probe` returns a
:class:`ProbeReport`, which knows how to describe itself. That keeps the
verdict testable without capturing output.
"""

from __future__ import annotations

from dataclasses import dataclass

from ipp_joblog.ipp import IppClient
from ipp_joblog.jobs import OPTIONAL_ATTRIBUTES, REQUIRED_ATTRIBUTES, Job

NO_HISTORY = (
    "This printer keeps no completed-job history, so per-user page counts\n"
    "cannot be collected from it. Job history is optional in RFC 8011."
)


@dataclass(frozen=True, slots=True)
class AttributeSupport:
    """Whether the printer reported one attribute we care about."""

    name: str
    required: bool
    reported: bool

    def __str__(self) -> str:
        mark = "yes" if self.reported else "NO "
        need = "required" if self.required else "optional"
        return f"  {mark:<4} {self.name}  ({need})"


@dataclass(frozen=True, slots=True)
class ProbeReport:
    """What a printer answered, and whether that is enough."""

    endpoint: str
    retained_jobs: int
    attributes: tuple[AttributeSupport, ...] = ()
    sample_key: str = ""
    make_and_model: str = ""
    state: str = ""

    @property
    def missing_required(self) -> list[str]:
        return [item.name for item in self.attributes if item.required and not item.reported]

    @property
    def usable(self) -> bool:
        return bool(self.retained_jobs) and not self.missing_required

    def lines(self) -> list[str]:
        """Everything worth printing to stdout, in order."""
        head = [
            f"{label}: {value}"
            for label, value in (("printer", self.make_and_model), ("state", self.state))
            if value
        ]
        head.append(f"asking {self.endpoint} for completed jobs (Get-Jobs, which-jobs=completed)")
        head.append(f"retained completed jobs: {self.retained_jobs}")
        if not self.retained_jobs:
            return head
        return [
            *head,
            *(str(item) for item in self.attributes),
            f"job identity looks like: {self.sample_key}",
        ]

    def problem(self) -> str:
        """The reason this printer will not do, or an empty string."""
        if not self.retained_jobs:
            return f"\n{NO_HISTORY}"
        if self.missing_required:
            return (
                f"\nMissing required attribute(s): {', '.join(self.missing_required)}. "
                "Page counts would be meaningless."
            )
        return ""


# What the printer says about itself, worth showing on its page. Anything the
# printer does not report is simply absent.
PRINTER_FACTS = (
    "printer-make-and-model",
    "printer-name",
    "printer-info",
    "printer-location",
    "printer-dns-sd-name",
    "printer-firmware-string-version",
    "printer-state-reasons",
    "printer-more-info",
    "printer-more-info-manufacturer",
    "ipp-versions-supported",
    "which-jobs-supported",
    "printer-up-time",
)


def collect_facts(client: IppClient, host: str) -> dict[str, str]:
    """Read the printer's self-description once, to store alongside its jobs.

    Called at startup only: a printer's identity does not change while we watch
    it, and this saves the dashboard from querying every printer it lists.
    """
    attributes = client.printer_attributes()
    facts = {"host": host, "endpoint": client.printer_uri}
    for name in PRINTER_FACTS:
        attribute = attributes.get(name)
        if attribute is None or attribute.value is None:
            continue
        facts[name] = ", ".join(str(value) for value in attribute.values)
    return facts


def probe(client: IppClient) -> ProbeReport:
    """Ask a printer everything needed to judge it. Assumes the path is set."""
    attributes = client.printer_attributes()

    def printer_value(name: str) -> str:
        return str(attributes[name].value) if name in attributes else ""

    groups = client.get_jobs(which_jobs="completed", limit=500)
    if not groups:
        return ProbeReport(
            endpoint=client.printer_uri,
            retained_jobs=0,
            make_and_model=printer_value("printer-make-and-model"),
            state=printer_value("printer-state-reasons"),
        )

    reported = set().union(*(group.keys() for group in groups))
    support = tuple(
        AttributeSupport(name=name, required=name in REQUIRED_ATTRIBUTES, reported=name in reported)
        for name in REQUIRED_ATTRIBUTES + OPTIONAL_ATTRIBUTES
    )
    return ProbeReport(
        endpoint=client.printer_uri,
        retained_jobs=len(groups),
        attributes=support,
        sample_key=Job.from_attributes(groups[0]).key,
        make_and_model=printer_value("printer-make-and-model"),
        state=printer_value("printer-state-reasons"),
    )
