"""The fast screen (DESIGN.md 6.2 step 3): a model taught by Claude's labels that reads every market in
milliseconds.

The design's screen is Laya, a small open-source decision model fine-tuned on the labels, with Jev as the baseline.
Neither is reachable from this build yet: Laya's weights come from Hugging Face, and Jev is a paid early-access API.
So the screen here is the simplest model that can learn from the labels, ``Words``: the words and word pairs of a
market's text, weighted by how rare they are (TF-IDF, hashed into 2^18 slots), with a logistic regression for
"material" and a ridge regression for each factor's rough move. It trains in about a second, so every run refits it
from the labels file rather than storing it. E0 measures it the way it would measure Laya, so a fine-tuned Laya
replaces it only where it does better.
"""

from __future__ import annotations

import zlib
from collections import Counter
from dataclasses import dataclass
from typing import Sequence

import numpy as np
from scipy.optimize import minimize
from scipy.sparse import csr_matrix, diags
from scipy.sparse.linalg import lsqr
from scipy.special import expit

from ..factors import NF
from ..matching.retrieve import tokenize

D = 1 << 18  # hashed feature slots


def _tokens(text: str) -> list[str]:
    w = tokenize(text)
    return w + [f"{a} {b}" for a, b in zip(w, w[1:])]


def counts(texts: Sequence[str]) -> csr_matrix:
    """Term counts, one row per text, words and word pairs hashed into ``D`` slots."""
    rows: list[int] = []
    cols: list[int] = []
    vals: list[float] = []
    for i, t in enumerate(texts):
        c = Counter(zlib.crc32(tok.encode()) % D for tok in _tokens(t))
        rows += [i] * len(c)
        cols += list(c)
        vals += list(c.values())
    return csr_matrix((np.array(vals, float), (np.array(rows, int), np.array(cols, int))), shape=(len(texts), D))


@dataclass
class Words:
    l2: float = 1.0  # on the logistic weights, against the class-balanced sum of losses
    ridge: float = 3.0  # on the rough moves' weights
    cols: np.ndarray | None = None
    idf: np.ndarray | None = None
    w: np.ndarray | None = None
    b: float = 0.0
    M: np.ndarray | None = None
    m0: np.ndarray | None = None

    def _x(self, texts: Sequence[str]) -> csr_matrix:
        X = counts(texts)[:, self.cols].tocsr()
        X.data = 1 + np.log(X.data)  # a word's tenth use says less than its first
        X = (X @ diags(self.idf)).tocsr()
        norm = np.sqrt(np.asarray(X.multiply(X).sum(axis=1)).ravel())
        return (diags(1 / np.where(norm > 0, norm, 1.0)) @ X).tocsr()

    def fit(self, texts: Sequence[str], material: np.ndarray, moves: np.ndarray) -> "Words":
        n = len(texts)
        df = np.asarray((counts(texts) > 0).sum(axis=0)).ravel()
        self.cols = np.flatnonzero(df)
        self.idf = np.log((1 + n) / (1 + df[self.cols])) + 1
        X = self._x(texts)
        y = np.asarray(material, float)
        s = 2 * y - 1
        pos = y.sum()
        cw = np.where(y > 0, n / (2 * max(pos, 1)), n / (2 * max(n - pos, 1)))  # both classes weigh the same

        def loss(theta):
            w, b = theta[:-1], theta[-1]
            m = -s * (X @ w + b)
            g = cw * -s * expit(m)
            return float((cw * np.logaddexp(0, m)).sum() + self.l2 / 2 * w @ w), np.append(X.T @ g + self.l2 * w, g.sum())

        res = minimize(loss, np.zeros(X.shape[1] + 1), jac=True, method="L-BFGS-B", options={"maxiter": 1000})
        self.w, self.b = res.x[:-1], float(res.x[-1])
        mv = np.asarray(moves, float)
        self.m0 = mv.mean(axis=0) if n else np.zeros(NF)
        self.M = np.column_stack([lsqr(X, mv[:, k] - self.m0[k], damp=np.sqrt(self.ridge))[0] for k in range(NF)])
        return self

    def predict(self, texts: Sequence[str]) -> tuple[np.ndarray, np.ndarray]:
        """The chance each market is material, and its rough move per factor (−2 to +2)."""
        if not len(texts):
            return np.zeros(0), np.zeros((0, NF))
        X = self._x(texts)
        return expit(X @ self.w + self.b), np.clip(X @ self.M + self.m0, -2, 2)
