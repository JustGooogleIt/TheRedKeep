"""The match judge: Claude with a fixed rubric and structured output (DESIGN.md 6.3 step 2).

One call per event judges all of its candidates. Every request and response is
saved under the cache directory, keyed by the rubric version, the model and the
exact request, so a rerun returns the same verdicts and any number can be
traced back to the call that produced it.

Calls opt into server-side fallback (``fallbacks: "default"``): if the model
declines a request for policy reasons, the API retries it on Anthropic's
recommended fallback model and the response records which model answered.
A refusal that survives the fallback raises ``JudgeRefused``.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any, Literal, Sequence

from pydantic import BaseModel, Field

from ..events import EventSpec
from ..markets import Market

RUBRIC_VERSION = "match-rubric-v1"
FALLBACK_BETA = "server-side-fallback-2026-07-01"

RUBRIC = """\
You judge whether prediction-market contracts measure a specific event. You see the event's exact terms and \
candidate markets with their resolution rules. Judge each candidate on four checks, then label the relationship. \
Be literal: the resolution rules decide what a market measures, not its title.

Checks
1. same_entity: is the market about the same thing (country, institution, company, statistic or actor) and the \
same kind of outcome?
2. threshold: if the event has a threshold, is the market's equal, stricter (harder to meet) or looser (easier to \
meet)? Use different_measure when the market measures another quantity, and not_applicable when neither has one.
3. deadline: does the market's question end on the same date as the event's deadline, earlier or later? Gaps that \
cannot change the answer (a market that settles on the release date of the same report, a day either way) count as same.
4. resolution_source: same, compatible (another source reporting the same facts), different or unclear.

Polarity
yes if the market's YES side corresponds to the event happening; no if its NO side does (for example, the event is \
"the Fed cuts" and the market asks "Will the Fed hold?" in a group where only cut and hold are realistic). Judge the \
relationship, thresholds and score for the side given by polarity.

Relationship
- same_question: the market resolves on that side exactly when the event happens, up to negligible differences.
- implies: the market resolving on that side guarantees the event, not the other way round (a stricter threshold, \
an earlier deadline or a narrower definition). Its probability is a lower bound for the event.
- implied_by: the event guarantees the market resolves on that side, not the other way round (a looser threshold, \
a later deadline or a broader definition). Its probability is an upper bound.
- part_of: one of several mutually exclusive outcomes in the same group whose union is exactly the event, such as \
"cut 25 bps" and "cut more than 25 bps" for the event "the Fed cuts". Label every outcome of the union this way.
- overlaps: related and correlated, but neither guarantees the other, so it cannot bound the event's probability.
- unrelated.

Score: how well the market measures the event's probability, from 0 to 1. For part_of, score how well the union of \
the parts measures it. Guide: 0.95 or more for identical terms; 0.8 to 0.95 for the same question with minor wording \
or source differences; 0.5 to 0.8 for a usable bound or ladder leg; under 0.5 for overlaps and unrelated markets. \
Liquidity and popularity never change the score.

Ladder fields: market_threshold is the market's threshold, in the event's units, when the market differs from the \
event only by its threshold; otherwise null. market_deadline is the market's deadline as YYYY-MM-DD when it differs \
from the event's; otherwise null.

quoted_rule: copy word for word the shortest sentence of the market's rules that your judgement relies on.
reason: one plain sentence a portfolio manager would understand, naming the decisive difference if there is one, \
for example "Closes Dec 31, 2026, four months before the event's deadline."

Judge every candidate, in the order given, and copy each candidate's id exactly."""


class Verdict(BaseModel):
    id: str
    relation: Literal["same_question", "implies", "implied_by", "part_of", "overlaps", "unrelated"]
    polarity: Literal["yes", "no"]
    same_entity: bool
    threshold: Literal["equal", "stricter", "looser", "different_measure", "not_applicable"]
    deadline: Literal["same", "earlier", "later"]
    resolution_source: Literal["same", "compatible", "different", "unclear"]
    market_threshold: float | None
    market_deadline: str | None
    score: float = Field(description="0 to 1")
    quoted_rule: str
    reason: str


class Judgement(BaseModel):
    verdicts: list[Verdict]


class JudgeRefused(RuntimeError):
    pass


def candidate_brief(m: Market) -> dict[str, Any]:
    return {
        "id": m.key,
        "venue": m.venue,
        "title": m.title,
        "outcome": m.outcome,
        "group": m.event_title,
        "group_outcomes_mutually_exclusive": m.exclusive,
        "closes": m.close_time.date().isoformat() if m.close_time else None,
        "rules": m.rules[:1500],
    }


def unjudged(m: Market, why: str) -> Verdict:
    return Verdict(id=m.key, relation="unrelated", polarity="yes", same_entity=False, threshold="not_applicable",
                   deadline="same", resolution_source="unclear", market_threshold=None, market_deadline=None,
                   score=0.0, quoted_rule="", reason=why)


class Judge:
    def __init__(self, client: Any, *, model: str, effort: str = "medium", log_dir: Path | None = None):
        self.client, self.model, self.effort, self.log_dir = client, model, effort, log_dir

    def request(self, spec: EventSpec, candidates: Sequence[Market], today: date) -> dict[str, Any]:
        return {"today": today.isoformat(), "event": spec.brief(), "candidates": [candidate_brief(m) for m in candidates]}

    def judge(self, spec: EventSpec, candidates: Sequence[Market], today: date) -> list[Verdict]:
        if not candidates:
            return []
        req = self.request(spec, candidates, today)
        body = json.dumps(req, indent=1, ensure_ascii=False)
        key = hashlib.sha256(f"{RUBRIC_VERSION}|{self.model}|{self.effort}|{body}".encode()).hexdigest()[:32]
        path = self.log_dir / "judge" / f"{spec.id}-{key}.json" if self.log_dir else None
        if path and path.exists():
            parsed = Judgement.model_validate(json.loads(path.read_text())["output"])
        else:
            resp = self.client.beta.messages.parse(
                model=self.model,
                max_tokens=16000,
                betas=[FALLBACK_BETA],
                fallbacks="default",
                thinking={"type": "adaptive"},
                output_config={"effort": self.effort},
                system=[{"type": "text", "text": RUBRIC, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": body}],
                output_format=Judgement,
            )
            if resp.stop_reason == "refusal":
                raise JudgeRefused(f"the judge declined event {spec.id}")
            if resp.stop_reason == "max_tokens" or resp.parsed_output is None:
                raise RuntimeError(f"the judge returned no verdicts for {spec.id} (stop_reason={resp.stop_reason})")
            parsed = resp.parsed_output
            if path:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps({
                    "rubric": RUBRIC_VERSION, "model": self.model, "served_by": getattr(resp, "model", None),
                    "request": req, "output": parsed.model_dump(),
                    "usage": resp.usage.model_dump() if getattr(resp, "usage", None) else None,
                }, indent=1, ensure_ascii=False))
        got = {v.id: v for v in parsed.verdicts}
        out = []
        for m in candidates:
            v = got.get(m.key)
            if v is None:
                out.append(unjudged(m, "The judge did not return a verdict for this market."))
            else:
                out.append(v.model_copy(update={"score": min(max(v.score, 0.0), 1.0)}))
        return out
