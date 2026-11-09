"""Eval E0 (DESIGN.md section 10): does the cheap screen keep the markets that matter? Fixed before the first run.

* **The split.** Claude's labels of the even sample, 70/30 by a hash of each market's group, so a ladder's rungs
  never sit on both sides. Markets labeled because the screen was unsure about them are left out of E0: they aren't
  an even sample.
* **The operating point,** set on the training markets alone from 5-fold predictions (folds by group): below the
  line that keeps ``TARGET_RECALL`` of the material markets the screen drops a market; above the line where
  ``TARGET_PRECISION`` of them are material it keeps it; the band between goes to Claude.
* **On the test markets:** the recall of material markets (kept, or sent to Claude, whose label is the reference),
  the shares dropped, kept and sent on, the precision of those kept, the ranking's average precision and AUC, and
  recall and precision when the screen alone keeps its top 10, 20, 30 or 50%. The rough moves: per factor, how often
  the screen's sign matches Claude's where Claude says the factor moves.
* **Cost and speed per 1,000 markets:** Claude's tokens and seconds from the labeling calls, against the screen's
  seconds plus Claude's on the markets it sends on.
"""

from __future__ import annotations

import hashlib
import time
from typing import Sequence

import numpy as np

from ..factors import FACTORS, NF
from .labels import Row
from .screen import Words

TARGET_RECALL = 0.97
TARGET_PRECISION = 0.90
TEST = 0.3
FOLDS = 5
TOPS = (0.1, 0.2, 0.3, 0.5)


def _bucket(group: str, n: int, seed: str) -> int:
    return int(hashlib.sha256(f"{seed}|{group}".encode()).hexdigest(), 16) % n


def split(rows: Sequence[Row]) -> tuple[list[Row], list[Row]]:
    """Training and test markets of the even sample, by a hash of the group."""
    even = [r for r in rows if r.source == "sample"]
    test = [r for r in even if _bucket(r.group, 1000, "e0-split") < TEST * 1000]
    keys = {r.key for r in test}
    return [r for r in even if r.key not in keys], test


def arrays(rows: Sequence[Row]) -> tuple[list[str], np.ndarray, np.ndarray]:
    return [r.text for r in rows], np.array([r.material for r in rows]), np.array([r.moves for r in rows], float).reshape(-1, NF)


def fit(rows: Sequence[Row]) -> Words:
    return Words().fit(*arrays(rows))


def out_of_fold(rows: Sequence[Row]) -> np.ndarray:
    """Each market's chance of being material from a screen that never saw its group."""
    fold = np.array([_bucket(r.group, FOLDS, "e0-folds") for r in rows])
    p = np.zeros(len(rows))
    for k in range(FOLDS):
        held = fold == k
        if held.any() and (~held).any():
            p[held] = fit([r for r, h in zip(rows, held) if not h]).predict([r.text for r, h in zip(rows, held) if h])[0]
    return p


def thresholds(p: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    """``(drop, keep)``: below ``drop`` the screen drops a market, at or above ``keep`` it keeps it."""
    if not y.any():
        return 1.0, 1.0
    order = np.argsort(-p)
    ps, ys = p[order], y[order]
    found = np.cumsum(ys) / ys.sum()
    drop = float(ps[np.argmax(found >= TARGET_RECALL)])
    prec = np.cumsum(ys) / np.arange(1, len(ys) + 1)
    ok = [i for i in range(len(ps)) if prec[i] >= TARGET_PRECISION and ps[i] >= drop and (i + 1 == len(ps) or ps[i + 1] < ps[i])]
    keep = float(ps[max(ok)]) if ok else float("inf")  # the lowest line that still holds the precision
    return drop, max(keep, drop)


def average_precision(p: np.ndarray, y: np.ndarray) -> float | None:
    if not y.any():
        return None
    order = np.argsort(-p)
    ys = y[order]
    prec = np.cumsum(ys) / np.arange(1, len(ys) + 1)
    return float((prec * ys).sum() / ys.sum())


def auc(p: np.ndarray, y: np.ndarray) -> float | None:
    pos, neg = p[y], p[~y]
    if not len(pos) or not len(neg):
        return None
    ranks = np.argsort(np.argsort(np.concatenate([pos, neg]))) + 1.0
    return float((ranks[:len(pos)].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))


def operating(p: np.ndarray, y: np.ndarray, drop: float, keep: float) -> dict:
    kept, band = p >= keep, (p >= drop) & (p < keep)
    n, mat = len(p), int(y.sum())
    return {"markets": n, "material": mat,
            "dropped": int((~kept & ~band).sum()), "kept": int(kept.sum()), "to_claude": int(band.sum()),
            "recall": float((y & (kept | band)).sum() / mat) if mat else None,
            "recall_screen_alone": float((y & kept).sum() / mat) if mat else None,
            "precision_kept": float(y[kept].mean()) if kept.any() else None,
            "material_dropped": int((y & ~kept & ~band).sum())}


def tops(p: np.ndarray, y: np.ndarray) -> list[dict]:
    order = np.argsort(-p)
    out = []
    for f in TOPS:
        k = max(1, int(round(f * len(p))))
        hit = y[order[:k]]
        out.append({"top": f, "kept": k, "recall": float(hit.sum() / y.sum()) if y.any() else None,
                    "precision": float(hit.mean())})
    return out


def move_signs(pred: np.ndarray, rows: Sequence[Row]) -> dict:
    """Per factor, where Claude says the factor moves: how often the screen's rough move has the same sign."""
    lab = np.array([r.moves for r in rows], float).reshape(-1, NF)
    out = {}
    for k, f in enumerate(FACTORS):
        m = np.abs(lab[:, k]) >= 1
        if m.any():
            out[f.key] = {"moves": int(m.sum()), "sign_right": int((np.sign(pred[m, k]) == np.sign(lab[m, k])).sum())}
    return out


def cost(rows: Sequence[Row], share_to_claude: float, screen_seconds: float) -> dict:
    """Per 1,000 markets: Claude on every one, against the screen plus Claude on the ones it sends on."""
    tin = float(np.mean([r.tokens_in for r in rows])) if rows else 0.0
    tout = float(np.mean([r.tokens_out for r in rows])) if rows else 0.0
    secs = float(np.mean([r.seconds for r in rows])) if rows else 0.0
    return {"claude_every_market": {"tokens_in": 1000 * tin, "tokens_out": 1000 * tout, "seconds": 1000 * secs},
            "screen": {"seconds": screen_seconds, "tokens_in": 1000 * share_to_claude * tin,
                       "tokens_out": 1000 * share_to_claude * tout,
                       "claude_seconds": 1000 * share_to_claude * secs}}


def study(rows: Sequence[Row]) -> dict:
    train, test = split(rows)
    _, y_tr, _ = arrays(train)
    texts, y, _ = arrays(test)
    drop, keep = thresholds(out_of_fold(train), y_tr)
    model = fit(train)
    t0 = time.perf_counter()
    p, mv = model.predict(texts)
    per_1000 = (time.perf_counter() - t0) / max(len(texts), 1) * 1000
    op = operating(p, y, drop, keep)
    return {
        "labels": len(rows), "even_sample": len(train) + len(test),
        "train": {"markets": len(train), "groups": len({r.group for r in train}), "material": int(y_tr.sum())},
        "test": {"markets": len(test), "groups": len({r.group for r in test}), "material": int(y.sum())},
        "targets": {"recall": TARGET_RECALL, "precision": TARGET_PRECISION},
        "thresholds": {"drop": drop, "keep": keep if np.isfinite(keep) else None},
        "operating": op,
        "average_precision": average_precision(p, y), "auc": auc(p, y),
        "tops": tops(p, y),
        "moves": move_signs(mv, test),
        "cost_per_1000": cost(train + test, op["to_claude"] / max(op["markets"], 1), per_1000),
        "scores": [{"key": r.key, "p": float(v)} for r, v in zip(test, p)],
    }
