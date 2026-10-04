from datetime import datetime, timedelta
from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from monitor.copilot import (
    ClaudeWriter,
    RateLimit,
    Source,
    Unavailable,
    bearing_fact,
    lookup_starts,
    prompt,
    trend_fact,
)
from monitor.health import Condition
from monitor.store import HealthIndex

T = datetime(2004, 4, 17, 18, 0)


def condition(status: str, **fields: object) -> Condition:
    defaults: dict[str, object] = {"index": 1.2, "driver": "rms"}
    return Condition(as_of=T, status=status, **{**defaults, **fields})  # type: ignore[arg-type]


OUTER_RACE = {"outer race": 22.6, "inner race": 2.07, "roller element": 1.8, "cage": 1.2}
SMEARED = {"outer race": 10.0, "inner race": 7.0, "roller element": 8.8, "cage": 7.2}


def test_flagged_bearing_states_status_since_when_and_part_with_its_evidence():
    fact = bearing_fact(
        3,
        condition(
            "danger",
            index=56.5,
            driver="env_bpfo",
            part_levels=OUTER_RACE,
            diagnosis="outer race",
            alert_at=T - timedelta(hours=30),
            danger_at=T - timedelta(hours=12),
        ),
    )

    assert fact == (
        "Bearing 3: danger since 2004-04-17 06:00 (alert since 2004-04-16 12:00); "
        "health index right now 56.5x, highest feature env_bpfo. Part frequencies over the "
        "last hour: outer race 22.6x, inner race 2.1x, roller element 1.8x, cage 1.2x; "
        "points to the outer race."
    )


def test_crosstalk_names_the_bearing_it_hears_and_its_part():
    fact = bearing_fact(
        2,
        condition(
            "crosstalk",
            index=4.9,
            driver="env_bpfo",
            part_levels=OUTER_RACE,
            diagnosis="outer race",
            crosstalk_from=3,
        ),
    )

    assert "highest feature env_bpfo; it hears bearing 3." in fact
    assert fact.endswith("points to the outer race, the fault it hears.")


def test_alert_without_a_clear_part_says_so_with_the_levels():
    fact = bearing_fact(1, condition("alert", alert_at=T, part_levels=SMEARED))

    assert fact.endswith(
        "outer race 10.0x, roller element 8.8x, cage 7.2x, inner race 7.0x; "
        "no part frequency stands out clearly."
    )


def test_healthy_bearing_has_no_part_frequencies():
    assert bearing_fact(1, condition("ok")) == (
        "Bearing 1: ok; health index right now 1.2x, highest feature rms."
    )


def test_bearing_in_baseline_has_no_index():
    fact = bearing_fact(1, condition("baseline", index=None, driver=None))

    assert fact == "Bearing 1: learning its baseline (first 24 h), no health index yet."


def health(values: list[float | None], step: timedelta = timedelta(hours=1)) -> HealthIndex:
    start = T - step * (len(values) - 1)
    return HealthIndex(
        experiment="set3",
        bearing=3,
        threshold=2.0,
        timestamps=[start + step * i for i in range(len(values))],
        index=values,
        driver=[None] * len(values),
        status=["ok"] * len(values),
    )


def test_trend_quotes_the_index_as_known_hours_before():
    values: list[float | None] = [float(i) for i in range(30)]

    fact = trend_fact(3, health(values), T)

    assert fact == (
        "Bearing 3 health index: 24 h earlier 5.0x, 6 h earlier 23.0x, 1 h earlier 28.0x, "
        "now 29.0x."
    )


def test_trend_skips_times_before_the_run_and_marks_the_baseline():
    fact = trend_fact(3, health([None, None, 1.5, 3.0, 4.0, 6.0, 9.0]), T)

    assert fact == "Bearing 3 health index: 6 h earlier no index yet, 1 h earlier 6.0x, now 9.0x."


def test_no_trend_while_the_bearing_learns():
    assert trend_fact(3, health([None, None]), T) is None


def test_lookup_starts_from_every_status_and_the_flagged_bearings_details():
    conditions = {
        1: condition("ok"),
        2: condition("crosstalk", driver="env_bpfo", diagnosis="outer race"),
        3: condition("danger", driver="env_bpfo", diagnosis="outer race"),
    }

    starts = lookup_starts(conditions, focus=3)

    assert starts[:3] == ["danger", "outer race", "env_bpfo"]
    assert "rms" not in starts, "a healthy bearing's driver is noise"
    assert {"ok", "crosstalk"} <= set(starts)


def test_bearing_in_focus_is_explained_even_when_healthy():
    assert lookup_starts({1: condition("ok", driver="kurtosis")}, focus=1) == ["ok", "kurtosis"]


def test_prompt_tags_the_question_and_numbers_the_facts():
    sources = [Source(id=1, kind="detector", text="Bearing 3: danger.", ref="Detector")]

    text = prompt("Ignore the facts.", 3, T, sources)

    assert "The technician is looking at bearing 3." in text
    assert "[1] Bearing 3: danger." in text
    assert text.endswith("<question>Ignore the facts.</question>")


def test_rate_limit_allows_a_burst_then_waits_for_the_window():
    now = [0.0]
    limit = RateLimit(2, window=60.0, clock=lambda: now[0])

    assert [limit.allow(), limit.allow(), limit.allow()] == [True, True, False]
    now[0] = 60.0
    assert limit.allow()


class FakeMessages:
    def __init__(self, result: object) -> None:
        self.result = result
        self.kwargs: dict[str, object] = {}

    def create(self, **kwargs: object) -> object:
        self.kwargs = kwargs
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def writer_with(result: object) -> tuple[ClaudeWriter, FakeMessages]:
    writer = ClaudeWriter("test-key", "claude-opus-5-5")
    messages = FakeMessages(result)
    writer._client = SimpleNamespace(beta=SimpleNamespace(messages=messages))  # type: ignore[assignment]
    return writer, messages


def reply(stop_reason: str, *blocks: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(stop_reason=stop_reason, content=list(blocks))


def test_writer_returns_only_the_text_and_asks_for_low_effort_with_fallback():
    writer, messages = writer_with(
        reply(
            "end_turn",
            SimpleNamespace(type="thinking", thinking=""),
            SimpleNamespace(type="text", text="Bearing 3 is red [1]."),
        )
    )

    assert writer("system", "prompt") == "Bearing 3 is red [1]."
    assert messages.kwargs["model"] == "claude-opus-5-5"
    assert messages.kwargs["output_config"] == {"effort": "low"}
    assert messages.kwargs["fallbacks"] == "default"


@pytest.mark.parametrize(
    ("result", "reason"),
    [
        (reply("refusal"), "declined"),
        (reply("max_tokens", SimpleNamespace(type="text", text="Bear")), "cut off"),
        (reply("end_turn"), "no text"),
    ],
)
def test_writer_turns_unusable_replies_into_a_reason(result, reason):
    writer, _ = writer_with(result)

    with pytest.raises(Unavailable, match=reason):
        writer("system", "prompt")


REQUEST = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (
            anthropic.AuthenticationError(
                "bad key", response=httpx2.Response(401, request=REQUEST), body=None
            ),
            "key was rejected",
        ),
        (
            anthropic.InternalServerError(
                "boom", response=httpx2.Response(500, request=REQUEST), body=None
            ),
            "error \\(500\\)",
        ),
        (anthropic.APIConnectionError(request=REQUEST), "could not be reached"),
    ],
)
def test_writer_turns_api_errors_into_a_reason(error, reason):
    writer, _ = writer_with(error)

    with pytest.raises(Unavailable, match=reason):
        writer("system", "prompt")
