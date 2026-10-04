"""The copilot: answers a question about one run at one moment, from looked-up facts only.

Retrieval-augmented generation: the facts come from the detector, for every bearing as known at
that moment, and from the knowledge graph. The language model only phrases them and cites each
one; the detector's status stays the verdict. Users only ever see "the copilot": the model is one
adapter and a setting, so it can be swapped.
"""

import threading
import time
from bisect import bisect_right
from collections import deque
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from itertools import pairwise
from typing import Annotated, Literal, Protocol

import anthropic
from pydantic import BaseModel, StringConstraints

from monitor.health import HOLD, MAX_GAP, Condition, machine_condition
from monitor.knowledge import Knowledge
from monitor.store import FeatureStore, HealthIndex

# Operating hours before the moment at which the index is quoted, so "is it getting worse?" has an
# answer. Damage only grows while the rig runs, so stops do not count, as in the evaluation.
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
on, like [2] or [1][4]. The detector's status is the verdict: explain it, never overrule it. A \
general fact that starts with "For bearing ..." applies to each bearing it names. Quote numbers \
exactly as the facts give them and never estimate new ones, such as a remaining life. If the facts \
do not answer the question, say so in one sentence and name what is missing.

Answer the question in the first sentence, then add only what the technician needs to act on it. \
Answer in the language of the question, as short as the question allows and in at most 80 words \
of plain text, without headings or lists."""


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
    """The model gave no usable answer. The message says why, in words for the technician."""


class UnknownBearing(LookupError):
    pass


class RateLimited(Exception):
    pass


class Writer(Protocol):
    def __call__(self, system: str, prompt: str) -> str: ...


class ClaudeWriter:
    """The adapter for Anthropic's API, the only place that names the vendor. One call per
    question: no agent loop, because the facts are chosen beforehand."""

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
            raise Unavailable("The copilot is busy. Try again in a minute.") from exc
        except anthropic.APIStatusError as exc:
            raise Unavailable(
                f"The copilot's language model answered with an error ({exc.status_code})."
            ) from exc
        except anthropic.APIConnectionError as exc:
            raise Unavailable("The copilot's language model could not be reached.") from exc
        if response.stop_reason == "refusal":
            raise Unavailable("The copilot declined to answer this question.")
        if response.stop_reason == "max_tokens":
            raise Unavailable("The answer was cut off.")
        text = "".join(block.text for block in response.content if block.type == "text").strip()
        if not text:
            raise Unavailable("The copilot returned no text.")
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


def _listed(bearings: list[int]) -> str:
    *rest, last = bearings
    return f"bearings {', '.join(map(str, rest))} and {last}" if rest else f"bearing {last}"


def rig_fact(conditions: Mapping[int, Condition]) -> str:
    """The rig as a whole, as the rig light shows it: it stops as a whole, for its worst bearing."""
    machine = machine_condition(dict(conditions))
    if machine.status == "ok":
        return "Rig: ok."
    if machine.status == "baseline":
        return "Rig: learning its baseline (first 24 h)."
    return f"Rig: {machine.status}, from {_listed(machine.bearings)}."


def bearing_fact(bearing: int, condition: Condition, name: Callable[[str], str]) -> str:
    """`name` turns the detector's words into what people call them: the model only writes words
    it is given, so it never sees a code name."""
    c = condition
    if c.index is None or c.driver is None:
        return f"Bearing {bearing}: learning its baseline (first 24 h), no health index yet."
    since = ""
    if c.danger_at is not None and c.alert_at is not None:
        since = f" since {_time(c.danger_at)} (alert since {_time(c.alert_at)})"
    elif c.alert_at is not None:
        since = f" since {_time(c.alert_at)}"
    # Two windows, said apart: the index is this snapshot, the part an hour's medians.
    text = (
        f"Bearing {bearing}: {c.status}{since}; health index right now {c.index:.1f}x, "
        f"highest feature: {name(c.driver)}"
    )
    if c.crosstalk_from is not None:
        text += f"; it hears bearing {c.crosstalk_from}"
        # The evidence, so the verdict can be checked rather than taken on trust.
        if c.crosstalk_feature is not None and c.crosstalk_factor is not None:
            text += (
                f", where the {name(c.crosstalk_feature)} rose {c.crosstalk_factor:.1f}x as much "
                "over the last hour"
            )
    text += "."
    if c.part_levels is None:
        return text
    # Highest first, so the margin that decides the part is easy to see.
    levels = sorted(c.part_levels.items(), key=lambda item: item[1], reverse=True)
    if c.diagnosis is None:
        verdict = "no part's signal stands out clearly"
    elif c.crosstalk_from is not None:
        verdict = f"points to the {c.diagnosis}, the fault it hears"
    else:
        verdict = f"points to the {c.diagnosis}"
    listed = ", ".join(f"{part} {level:.1f}x" for part, level in levels)
    return f"{text} Part signals over the last hour: {listed}; {verdict}."


def _running(timestamps: list[datetime]) -> list[timedelta]:
    """How long the rig had run at each snapshot: a step longer than MAX_GAP is a stop."""
    running = [timedelta(0)]
    for before, after in pairwise(timestamps):
        step = after - before
        running.append(running[-1] + (step if step <= MAX_GAP else timedelta(0)))
    return running


def trend_fact(bearing: int, health: HealthIndex, moment: datetime) -> str | None:
    """The index a few operating hours before the moment, each with the time it was measured, as
    known then. The last stop and the spread of the last hour are said, so that neither a stop nor
    one noisy snapshot reads as a trend."""
    now = bisect_right(health.timestamps, moment) - 1
    current = health.index[now] if now >= 0 else None
    if current is None:
        return None
    times, index = health.timestamps[: now + 1], health.index[: now + 1]
    running = _running(times)
    points, first = [], now
    for hours in TREND_HOURS:
        if running[now] < timedelta(hours=hours):
            continue
        i = bisect_right(running, running[now] - timedelta(hours=hours)) - 1
        first = min(first, i)
        value = "no index yet" if index[i] is None else f"{index[i]:.1f}x"
        unit = "operating hour" if hours == 1 else "operating hours"
        points.append(f"{hours} {unit} earlier ({_time(times[i])}) {value}")
    if not points:
        return None
    text = f"Bearing {bearing} health index: {', '.join(points)}, now {current:.1f}x"
    # The same hour the part signals cover, up to the latest snapshot.
    hour = [v for t, v in zip(times, index, strict=True) if t > times[now] - HOLD and v is not None]
    low, high = f"{min(hour):.1f}", f"{max(hour):.1f}"
    if low != high:
        text += f", between {low}x and {high}x over the last hour"
    text += "."
    stops = [(a, b) for a, b in pairwise(times[first:]) if b - a > MAX_GAP]
    if stops:
        before, after = stops[-1]
        text += f" The rig stood still between {_time(before)} and {_time(after)}."
    return text


def lookup_starts(conditions: Mapping[int, Condition], focus: int | None) -> dict[str, list[int]]:
    """Where the knowledge lookup starts, and for which bearings: every status shown, plus the part
    and driver of each flagged bearing and of the one in focus. The detector's words are the
    graph's node ids."""
    starts: dict[str, list[int]] = {}
    for bearing in sorted(conditions):
        c = conditions[bearing]
        starts.setdefault(c.status, []).append(bearing)
        if c.status in FLAGGED or bearing == focus:
            # A bearing that only hears a neighbour shows the neighbour's fault, not one of its own.
            owner = bearing if c.crosstalk_from is None else c.crosstalk_from
            for word in (c.diagnosis, c.driver):
                if word and owner not in starts.setdefault(word, []):
                    starts[word].append(owner)
    return {word: sorted(bearings) for word, bearings in starts.items()}


def reached_by(
    starts: Mapping[str, list[int]], origins: list[str], name: Callable[[str], str]
) -> str:
    """The bearings whose words led to a fact: general advice such as "replace the bearing" then
    says which bearings it concerns, instead of leaving that to the model."""
    words: dict[int, list[str]] = {}
    for origin in origins:
        for bearing in starts[origin]:
            words.setdefault(bearing, []).append(name(origin))
    return "For " + ", ".join(f"bearing {b} ({', '.join(w)})" for b, w in sorted(words.items()))


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
        """The numbered facts: the detector's view of the rig, then of every bearing in rig order,
        each with its trend, then the knowledge. The prompt names the bearing in focus, so the
        order never moves.
        """
        moment = max(c.as_of for c in conditions.values())
        ref = f"Detector, as of {_time(moment)}"
        name = self._knowledge.name
        detector = [rig_fact(conditions)]
        for b in sorted(conditions):
            detector.append(bearing_fact(b, conditions[b], name))
            health = self._store.health_index(experiment, b)
            trend = None if health is None else trend_fact(b, health, moment)
            if trend:
                detector.append(trend)
        starts = lookup_starts(conditions, focus)
        facts: list[tuple[Literal["detector", "knowledge"], str, str]] = [
            ("detector", text, ref) for text in detector
        ]
        facts += [
            ("knowledge", f"{reached_by(starts, origins, name)}: {f.text}", f.source)
            for f, origins in self._knowledge.around(starts)
        ]
        facts += [("knowledge", f.text, f.source) for f in self._knowledge.machine()]
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
