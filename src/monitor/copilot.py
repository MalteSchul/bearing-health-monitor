"""The copilot: answers a question about one run at one moment, from looked-up facts only.

Retrieval-augmented generation: the facts come from the detector, for every bearing as known at
that moment, and from the knowledge graph. Claude only phrases them and cites each one; the
detector's status stays the verdict.
"""

import threading
import time
from bisect import bisect_right
from collections import deque
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from typing import Annotated, Literal, Protocol

import anthropic
from pydantic import BaseModel, StringConstraints

from monitor.health import Condition
from monitor.knowledge import Knowledge
from monitor.store import FeatureStore, HealthIndex

# Hours before the moment at which the index is quoted, so "is it getting worse?" has an answer.
TREND_HOURS = (24, 6, 1)
# Only these have a part and a driver worth explaining; ok and baseline have nothing to explain.
FLAGGED = ("crosstalk", "alert", "danger")
NO_KEY = (
    "No API key is configured, so there is no written answer. The facts it would use are below."
)

SYSTEM = """\
You are the copilot of a vibration monitor for rolling bearings. A service technician asks about \
one test rig at one moment.

Answer only from the numbered facts in the message. After each sentence, cite the facts it rests \
on, like [2] or [1][4]. The detector's status is the verdict: explain it, never overrule it. Quote \
numbers exactly as the facts give them and never estimate new ones, such as a remaining life. If \
the facts do not answer the question, say so in one sentence and name what is missing.

Answer in the language of the question, in at most 120 words of plain text, without headings or \
lists."""


class Question(BaseModel):
    question: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
    # The bearing the technician is looking at, which "this bearing" in the question refers to.
    bearing: int | None = None


class Source(BaseModel):
    id: int
    kind: Literal["detector", "knowledge"]
    text: str
    ref: str


class CopilotAnswer(BaseModel):
    answer: str | None
    # Why there is no answer, e.g. no API key. The sources are returned either way.
    note: str | None = None
    sources: list[Source]


class Unavailable(Exception):
    """Claude gave no usable answer. The message says why, in words for the technician."""


class UnknownBearing(LookupError):
    pass


class RateLimited(Exception):
    pass


class Writer(Protocol):
    def __call__(self, system: str, prompt: str) -> str: ...


class ClaudeWriter:
    """One Claude call per question: no agent loop, because the facts are chosen beforehand."""

    def __init__(self, api_key: str, model: str) -> None:
        # The dashboard waits for the answer, so fail within a minute rather than retry for long.
        self._client = anthropic.Anthropic(api_key=api_key, timeout=45.0, max_retries=1)
        self._model = model

    def __call__(self, system: str, prompt: str) -> str:
        try:
            response = self._client.beta.messages.create(
                model=self._model,
                # Includes the model's thinking, which this model always does.
                max_tokens=4000,
                system=system,
                messages=[{"role": "user", "content": prompt}],
                # Phrasing given facts needs little reasoning; low keeps it quick and cheap.
                output_config={"effort": "low"},
                # A declined request is retried server-side on the model suited to its category.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except anthropic.AuthenticationError as exc:
            raise Unavailable("The API key was rejected.") from exc
        except anthropic.RateLimitError as exc:
            raise Unavailable("Claude is busy. Try again in a minute.") from exc
        except anthropic.APIStatusError as exc:
            raise Unavailable(f"Claude answered with an error ({exc.status_code}).") from exc
        except anthropic.APIConnectionError as exc:
            raise Unavailable("Claude could not be reached.") from exc
        if response.stop_reason == "refusal":
            raise Unavailable("Claude declined to answer this question.")
        if response.stop_reason == "max_tokens":
            raise Unavailable("The answer was cut off.")
        text = "".join(block.text for block in response.content if block.type == "text").strip()
        if not text:
            raise Unavailable("Claude returned no text.")
        return text


class RateLimit:
    """At most `limit` calls in any `window` seconds, across all users: the URL is public and
    every call costs money."""

    def __init__(
        self, limit: int, window: float = 60.0, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._limit = limit
        self._window = window
        self._clock = clock
        self._calls: deque[float] = deque()
        # Requests run in parallel threads.
        self._lock = threading.Lock()

    def allow(self) -> bool:
        with self._lock:
            now = self._clock()
            while self._calls and now - self._calls[0] >= self._window:
                self._calls.popleft()
            if len(self._calls) >= self._limit:
                return False
            self._calls.append(now)
            return True


def _time(t: datetime) -> str:
    return t.strftime("%Y-%m-%d %H:%M")


def bearing_fact(bearing: int, condition: Condition) -> str:
    c = condition
    if c.index is None:
        return f"Bearing {bearing}: learning its baseline (first 24 h), no health index yet."
    since = ""
    if c.danger_at is not None and c.alert_at is not None:
        since = f" since {_time(c.danger_at)} (alert since {_time(c.alert_at)})"
    elif c.alert_at is not None:
        since = f" since {_time(c.alert_at)}"
    # Two windows, said apart: the index is this snapshot, the part an hour's medians.
    text = (
        f"Bearing {bearing}: {c.status}{since}; health index right now {c.index:.1f}x, "
        f"highest feature {c.driver}"
    )
    if c.crosstalk_from is not None:
        text += f"; it hears bearing {c.crosstalk_from}"
    text += "."
    if c.part_levels is None:
        return text
    # Highest first, so the margin that decides the part is easy to see.
    levels = sorted(c.part_levels.items(), key=lambda item: item[1], reverse=True)
    if c.diagnosis is None:
        verdict = "no part frequency stands out clearly"
    elif c.crosstalk_from is not None:
        verdict = f"points to the {c.diagnosis}, the fault it hears"
    else:
        verdict = f"points to the {c.diagnosis}"
    listed = ", ".join(f"{part} {level:.1f}x" for part, level in levels)
    return f"{text} Part frequencies over the last hour: {listed}; {verdict}."


def trend_fact(bearing: int, health: HealthIndex, moment: datetime) -> str | None:
    """The index at a few times before the moment, as each was known then."""
    points = []
    for hours in TREND_HOURS:
        i = bisect_right(health.timestamps, moment - timedelta(hours=hours)) - 1
        if i < 0:
            continue
        value = health.index[i]
        points.append(f"{hours} h earlier {'no index yet' if value is None else f'{value:.1f}x'}")
    now = health.index[bisect_right(health.timestamps, moment) - 1]
    if now is None or not points:
        return None
    return f"Bearing {bearing} health index: {', '.join(points)}, now {now:.1f}x."


def lookup_starts(conditions: Mapping[int, Condition], focus: int | None) -> list[str]:
    """Where the knowledge lookup starts: every status shown, plus the part and driver of each
    flagged bearing and of the one in focus. The detector's words are the graph's node ids."""
    order = ([focus] if focus is not None else []) + [b for b in conditions if b != focus]
    starts: list[str] = []
    for bearing in order:
        c = conditions[bearing]
        starts.append(c.status)
        if c.status in FLAGGED or bearing == focus:
            starts += [word for word in (c.diagnosis, c.driver) if word]
    return starts


def prompt(question: str, focus: int | None, moment: datetime, sources: list[Source]) -> str:
    lines = [f"Moment: {_time(moment)} rig time."]
    if focus is not None:
        lines.append(f"The technician is looking at bearing {focus}.")
    lines += ["", "Facts:", *(f"[{s.id}] {s.text}" for s in sources)]
    # Tagged, so the question reads as data and not as further instructions.
    lines += ["", f"<question>{question}</question>"]
    return "\n".join(lines)


class Copilot:
    def __init__(
        self,
        store: FeatureStore,
        knowledge: Knowledge,
        writer: Writer | None,
        limit: RateLimit,
    ) -> None:
        self._store = store
        self._knowledge = knowledge
        self._writer = writer
        self._limit = limit

    def sources(
        self, experiment: str, conditions: Mapping[int, Condition], focus: int | None
    ) -> list[Source]:
        """The numbered facts: the detector's view of every bearing, then the knowledge."""
        moment = max(c.as_of for c in conditions.values())
        ref = f"Detector, as of {_time(moment)}"
        order = ([focus] if focus is not None else []) + [b for b in conditions if b != focus]
        detector = [bearing_fact(b, conditions[b]) for b in order]
        for b in order:
            health = self._store.health_index(experiment, b)
            trend = None if health is None else trend_fact(b, health, moment)
            if trend:
                detector.append(trend)
        knowledge = self._knowledge.around(lookup_starts(conditions, focus))
        facts: list[tuple[Literal["detector", "knowledge"], str, str]] = [
            ("detector", text, ref) for text in detector
        ]
        facts += [("knowledge", f.text, f.source) for f in [*knowledge, *self._knowledge.machine()]]
        return [
            Source(id=n, kind=kind, text=text, ref=r)
            for n, (kind, text, r) in enumerate(facts, start=1)
        ]

    def ask(self, experiment: str, question: Question, at: datetime | None) -> CopilotAnswer | None:
        """None for an unknown experiment."""
        conditions = self._store.conditions(experiment, at)
        if conditions is None:
            return None
        if question.bearing is not None and question.bearing not in conditions:
            raise UnknownBearing(f"No bearing {question.bearing} in experiment {experiment!r}")
        sources = self.sources(experiment, conditions, question.bearing)
        if self._writer is None:
            return CopilotAnswer(answer=None, note=NO_KEY, sources=sources)
        if not self._limit.allow():
            raise RateLimited
        moment = max(c.as_of for c in conditions.values())
        message = prompt(question.question, question.bearing, moment, sources)
        try:
            answer = self._writer(SYSTEM, message)
        except Unavailable as exc:
            return CopilotAnswer(answer=None, note=str(exc), sources=sources)
        return CopilotAnswer(answer=answer, sources=sources)
