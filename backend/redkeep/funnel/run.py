"""The funnel's three steps (DESIGN.md 6.2): Claude labels an even sample, E0 checks the screen it teaches, and the
screen reads the whole catalogue.

* ``label`` reads the live catalogue, applies the cheap filters, samples it evenly and has Claude label the sample,
  adding the rows to ``data/funnel/labels.jsonl``.
* ``e0`` runs eval E0 on the labels (see ``e0.py``).
* ``screen`` refits the screen on every label, sets its lines as E0 does, and screens the catalogue: it drops the
  markets below the lower line, keeps those above the upper one, and sends the band between to Claude, most likely
  first and up to a cap. Claude's labels of the band go into the labels file, marked as such, so the next fit learns
  from the markets it was unsure about. Kept markets are grouped by their venue's event (a ladder or outcome set is
  one question) with a rough factor vector and a reason.
"""

from __future__ import annotations

import csv
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import numpy as np

from ..config import DATA_DIR, Settings
from ..events import load_library
from ..factors import FACTORS, KEYS
from ..markets import Market
from . import e0
from .labels import LABEL_VERSION, Labeler, Row, append_rows, cheap_filters, group_of, read_rows, sample

LABELS = DATA_DIR / "funnel" / "labels.jsonl"
E0_FILE = DATA_DIR / "funnel" / "e0.json"
Progress = Callable[[str], None]


def _labeler(s: Settings, c) -> Labeler:
    if c.judge is None:
        raise RuntimeError("Claude's funnel labels need ANTHROPIC_API_KEY")
    return Labeler(c.judge.client, model=c.model, effort=s.effort, log_dir=s.cache_dir / "ai")


def _catalogue(c, progress: Progress) -> tuple[list[Market], dict]:
    from ..pipeline import catalogue

    now = datetime.now(timezone.utc)
    markets = catalogue(load_library(), c, now.date(), progress)
    f = cheap_filters(markets, now)
    progress(f"Cheap filters: {len(f.kept):,} of {len(markets):,} markets left ("
             + ", ".join(f"{n:,} {why}" for why, n in sorted(f.dropped.items(), key=lambda x: -x[1])) + " dropped)")
    return f.kept, {"listed": len(markets), "after_filters": len(f.kept), "dropped": f.dropped}


def label(s: Settings, c, *, n: int = 2000, path: Path = LABELS, progress: Progress = print) -> dict:
    markets, counts = _catalogue(c, progress)
    have = {r.key for r in read_rows(path) if r.rubric == LABEL_VERSION}
    picked = [m for m in sample(markets, n) if m.key not in have]
    progress(f"Sample: {n:,} markets, at most two per group; {len(picked):,} not labeled yet")
    rows, usage, missing = _labeler(s, c).label(picked, progress=progress)
    added = append_rows(path, rows)
    return {"as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"), "catalogue": counts,
            "sampled": len(picked), "labeled": len(rows), "missing": missing, "added": added,
            "material": sum(r.material for r in rows), "usage": asdict(usage), "path": str(path)}


def screen(s: Settings, c, *, claude_max: int = 400, path: Path = LABELS, progress: Progress = print) -> dict:
    rows = read_rows(path)
    even = [r for r in rows if r.source == "sample"]
    if len(even) < 50 or not any(r.material for r in even):
        raise RuntimeError(f"{path} needs at least 50 labeled markets, some material: run `redkeep funnel label`")
    drop, keep = e0.thresholds(e0.out_of_fold(even), np.array([r.material for r in even]))
    model = e0.fit(rows)
    markets, counts = _catalogue(c, progress)
    p, mv = model.predict([m.text() for m in markets])
    labeled = {r.key: r for r in rows}
    band = sorted((i for i in range(len(markets)) if drop <= p[i] < keep and markets[i].key not in labeled),
                  key=lambda i: -p[i])
    sent = [markets[i] for i in band[:claude_max]]
    new: list[Row] = []
    unlabeled = 0
    if sent:
        progress(f"Claude reads {len(sent):,} of the {len(band):,} markets the screen is unsure about")
        new, _, unlabeled = _labeler(s, c).label(sent, source="band", progress=progress)
        append_rows(path, new)
        labeled.update({r.key: r for r in new})

    verdicts = []
    for i, m in enumerate(markets):
        r = labeled.get(m.key)
        if r is not None:
            verdicts.append(("claude", r.material, r.moves, r.reason))
        elif p[i] >= keep:
            verdicts.append(("screen", True, [int(v) for v in np.round(mv[i])], ""))
        elif p[i] >= drop:
            verdicts.append(("unsure", None, [int(v) for v in np.round(mv[i])], ""))
        else:
            verdicts.append(("dropped", False, None, ""))

    groups: dict[str, dict] = {}
    for m, pi, (how, material, moves, reason) in zip(markets, p, verdicts):
        if not material and how != "unsure":
            continue
        g = groups.setdefault(group_of(m), {"group": group_of(m), "venue": m.venue, "title": m.event_title or m.title,
                                            "url": m.url, "markets": 0, "material": 0, "unsure": 0, "p": 0.0,
                                            "moves": [0] * len(KEYS), "reason": "", "by": "", "volume": 0.0,
                                            "closes": None, "examples": []})
        g["markets"] += 1
        g["material"] += bool(material)
        g["unsure"] += how == "unsure"
        g["p"] = max(g["p"], float(pi))
        g["volume"] += m.volume
        closes = m.close_time.date().isoformat() if m.close_time else None
        g["closes"] = min(filter(None, [g["closes"], closes]), default=None)
        if len(g["examples"]) < 3:
            g["examples"].append(m.title)
        if material and sum(map(abs, moves)) > sum(map(abs, g["moves"])):
            g["moves"], g["reason"], g["by"] = list(moves), reason or g["reason"], how
        elif reason and not g["reason"]:
            g["reason"] = reason
    ranked = sorted(groups.values(), key=lambda g: (-(g["material"] > 0), -max(map(abs, g["moves"])),
                                                    -sum(map(abs, g["moves"])), -g["volume"]))
    how = [v[0] for v in verdicts]
    return {
        "kind": "redkeep.funnel", "version": 1, "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "catalogue": counts, "labels": len(rows), "thresholds": {"drop": drop, "keep": keep if np.isfinite(keep) else None},
        "screen": {"dropped": how.count("dropped"), "kept": how.count("screen"), "claude": how.count("claude"),
                   "unsure_unchecked": how.count("unsure"), "band": len(band), "sent_to_claude": len(sent),
                   "new_labels": len(new), "not_labeled": unlabeled},
        "material_markets": sum(1 for v in verdicts if v[1]),
        "groups": ranked,
    }


def label_cases(s: Settings, c, cases) -> dict[str, bool]:
    """Which resolved markets of the jump study (E3) Claude's funnel rubric calls material. Only titles are kept for
    them, so Claude reads the title and category; it may also know how they ended, which matters less for whether a
    market could move a factor than for how far."""
    ms = [Market(venue=x.venue, id=x.key.split(":", 1)[1], event_id=x.event, title=x.title, event_title=x.title,
                 outcome=None, rules=f"Category: {x.category}.", close_time=None, bid=None, ask=None, last=None,
                 volume=0.0, volume_24h=0.0, liquidity=0.0, exclusive=False, url="") for x in cases]
    rows, _, _ = _labeler(s, c).label(ms)
    return {r.key: r.material for r in rows}


def check_sheet(rows: list[Row], scores: dict[str, float], path: Path, n: int = 50) -> Path:
    """A sheet of test markets for a person to check Claude's labels against: the hand-checked sample of E0."""
    picked = sorted(rows, key=lambda r: e0._bucket(r.key, 10**9, "e0-check"))[:n]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["market", "title", "claude_material", *[f.key for f in FACTORS], "claude_reason", "screen_p",
                    "agree (y/n)", "note"])
        for r in picked:
            w.writerow([r.key, r.title, int(r.material), *r.moves, r.reason, f"{scores.get(r.key, float('nan')):.2f}", "", ""])
    return path
