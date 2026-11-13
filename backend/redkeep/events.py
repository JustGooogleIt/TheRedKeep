"""The event library's specs: what each event means, exactly (DESIGN.md 6.2 and 6.3)."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Literal

from .config import DATA_DIR


@dataclass(frozen=True)
class Analog:
    date: date
    days: int
    surprise: float
    note: str


@dataclass(frozen=True)
class EventSpec:
    id: str
    title: str
    short: str
    category: str
    question: str
    resolves: date
    deadline: date
    source: str
    search: tuple[str, ...]
    why: str
    threshold: float | None = None
    threshold_unit: str | None = None
    direction: Literal["above", "below"] | None = None
    analogs: tuple[Analog, ...] = ()
    order: int = 0

    def brief(self) -> dict:
        """What the matcher and the AI see about the event."""
        d = {
            "id": self.id, "question": self.question, "deadline": self.deadline.isoformat(),
            "resolution_source": self.source,
        }
        if self.threshold is not None:
            d["threshold"] = {"value": self.threshold, "unit": self.threshold_unit, "direction": self.direction}
        return d


@dataclass(frozen=True)
class LinkSpec:
    parent: str
    child: str
    w: float
    reason: str


@dataclass
class Library:
    events: list[EventSpec]
    links: list[LinkSpec] = field(default_factory=list)

    def by_id(self) -> dict[str, EventSpec]:
        return {e.id: e for e in self.events}


def parse_event(e: dict, order: int = 0) -> EventSpec:
    """One ``[[event]]`` table of a library or an episode file."""
    analogs = tuple(Analog(a["date"], int(a.get("days", 1)), float(a.get("surprise", 1.0)), a.get("note", ""))
                    for a in e.get("analogs", []))
    return EventSpec(
        id=e["id"], title=e["title"], short=e.get("short", e["title"]), category=e.get("category", ""),
        question=e["question"], resolves=e["resolves"], deadline=e.get("deadline", e["resolves"]),
        source=e.get("source", ""), search=tuple(e.get("search", [])), why=e.get("why", ""),
        threshold=e.get("threshold"), threshold_unit=e.get("threshold_unit"), direction=e.get("direction"),
        analogs=analogs, order=order,
    )


def parse_links(raw: list[dict], ids: set[str]) -> list[LinkSpec]:
    links = [LinkSpec(l["parent"], l["child"], float(l["w"]), l.get("reason", "")) for l in raw]
    for ln in links:
        if ln.parent not in ids or ln.child not in ids:
            raise ValueError(f"link {ln.parent} -> {ln.child} names an unknown event")
    return links


def still_open(lib: Library, today: date) -> tuple[Library, list[EventSpec]]:
    """The events whose deadline is today or later, with the links between them, and the ones left out.

    An event past its deadline has been decided: it is news, not a risk, and its market has settled. Kept, it would
    show as a live event with the AI prior's odds alone, since the venues no longer list it.
    """
    gone = [e for e in lib.events if e.deadline < today]
    keep = {e.id for e in lib.events} - {e.id for e in gone}
    return Library([e for e in lib.events if e.id in keep],
                   [ln for ln in lib.links if ln.parent in keep and ln.child in keep]), gone


def load_library(path: Path | None = None) -> Library:
    raw = tomllib.loads((path or DATA_DIR / "events.toml").read_text())
    events = [parse_event(e, i) for i, e in enumerate(raw.get("event", []))]
    ids = {e.id for e in events}
    if len(ids) != len(events):
        raise ValueError("duplicate event ids in the library")
    return Library(events, parse_links(raw.get("link", []), ids))
