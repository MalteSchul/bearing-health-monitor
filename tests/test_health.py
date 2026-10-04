import numpy as np
import pandas as pd
import pytest

from monitor.features import FEATURES
from monitor.health import (
    PARTS,
    Condition,
    History,
    assess,
    bearing_ratios,
    health_index,
    held,
    machine_condition,
)

START = pd.Timestamp("2004-02-12 10:00")


def run(hours: float = 72, pairs: tuple[tuple[int, int], ...] = ((1, 1), (2, 2))) -> pd.DataFrame:
    """A run with every feature at 1.0, one row per snapshot and (bearing, channel) pair."""
    times = pd.date_range(START, START + pd.Timedelta(hours=hours), freq="10min", inclusive="left")
    return pd.DataFrame(
        [
            {"timestamp": t, "bearing": b, "channel": c, **dict.fromkeys(FEATURES, 1.0)}
            for t in times
            for b, c in pairs
        ]
    )


def hour(h: float) -> pd.Timestamp:
    return START + pd.Timedelta(hours=h)


def between(frame: pd.DataFrame, start: float, end: float = 10_000) -> pd.Series:
    return (frame["timestamp"] >= hour(start)) & (frame["timestamp"] < hour(end))


def index_of(frame: pd.DataFrame, bearing: int) -> pd.DataFrame:
    return health_index(bearing_ratios(frame)).loc[bearing]


def index_series(
    values: list[float], step: str = "10min", start: pd.Timestamp = START
) -> pd.Series:
    """One bearing's health index, one value per snapshot."""
    return pd.Series(values, index=pd.date_range(start, periods=len(values), freq=step))


def fault(frame: pd.DataFrame, bearing: int, feature: str, ratio: float, start: float = 48) -> None:
    """Raise one feature of one bearing from `start` hours on."""
    frame.loc[between(frame, start) & (frame["bearing"] == bearing), feature] = ratio


def latest(frame: pd.DataFrame) -> dict[int, Condition]:
    return {h.bearing: h.conditions[-1] for h in assess(frame)}


def test_there_is_no_index_while_the_baseline_is_recorded():
    history = assess(run())[0]

    assert index_of(run(), 1)["health_index"][lambda s: s.index < hour(24)].isna().all()
    assert {c.status for c in history.conditions if c.as_of < hour(24)} == {"baseline"}
    assert {c.status for c in history.conditions if c.as_of >= hour(24)} == {"ok"}


def test_steady_run_has_index_one_whatever_each_channel_measures():
    frame = run()
    frame.loc[frame["channel"] == 2, list(FEATURES)] = 40.0

    assert health_index(bearing_ratios(frame))["health_index"].dropna().eq(1.0).all()


def test_baseline_is_the_first_24_hours_only():
    frame = run()
    frame.loc[between(frame, 0, 24), "rms"] = 0.5

    assert index_of(frame, 1)["health_index"].dropna().eq(2.0).all()


def test_one_spike_in_the_baseline_does_not_shift_it():
    frame = run()
    frame.loc[0, "kurtosis"] = 100.0

    assert index_of(frame, 1)["health_index"].iloc[-1] == 1.0


def test_index_names_the_feature_that_rose():
    frame = run()
    fault(frame, 1, "env_bpfi", 3.0)

    last = index_of(frame, 1).iloc[-1]

    assert (last["health_index"], last["feature"]) == (3.0, "env_bpfi")
    assert index_of(frame, 2)["health_index"].dropna().eq(1.0).all()


def test_bearing_is_as_bad_as_its_worse_channel():
    frame = run(pairs=((1, 1), (1, 2)))
    frame.loc[between(frame, 48) & (frame["channel"] == 2), "env_bpfo"] = 4.0

    last = index_of(frame, 1).iloc[-1]

    assert (last["health_index"], last["feature"]) == (4.0, "env_bpfo")


def test_index_must_stay_above_threshold_for_a_full_hour():
    # 10-minute snapshots: six above span 50 minutes, the seventh completes the hour.
    assert not held(index_series([1.0] + [2.5] * 6 + [1.0] * 5)).any()

    holding = held(index_series([1.0] + [2.5] * 7 + [1.0] * 5))

    assert holding[holding].index.tolist() == [holding.index[7]]


def test_dip_below_threshold_restarts_the_hour():
    assert not held(index_series([2.5] * 4 + [1.9] + [2.5] * 4)).any()


def test_hold_is_measured_in_time_not_snapshots():
    # Seven snapshots at 5 minutes cover only half an hour.
    assert not held(index_series([2.5] * 7, step="5min")).any()
    assert held(index_series([2.5] * 13, step="5min")).any()


def test_gap_in_the_recording_restarts_the_hour():
    before_pause = index_series([2.5] * 4)
    after_pause = index_series([2.5] * 4, start=START + pd.Timedelta(days=6))

    assert not held(pd.concat([before_pause, after_pause])).any()


def test_healthy_run_stays_ok():
    assert [c.status for c in latest(run()).values()] == ["ok", "ok"]


def test_failing_bearing_alerts_with_its_part():
    frame = run()
    fault(frame, 1, "env_bpfo", 38.0)

    condition = latest(frame)[1]

    assert (condition.status, condition.driver, condition.diagnosis) == (
        "alert",
        "env_bpfo",
        "outer race",
    )
    assert condition.alert_at == hour(49)


def test_alert_latches_although_the_index_drops_again():
    frame = run()
    fault(frame, 1, "env_bpfo", 38.0)
    frame.loc[between(frame, 50), "env_bpfo"] = 1.0

    condition = latest(frame)[1]

    assert (condition.status, condition.index, condition.alert_at) == ("alert", 1.0, hour(49))


def test_time_domain_fault_alerts_without_naming_a_part():
    frame = run()
    fault(frame, 1, "kurtosis", 5.0)

    condition = latest(frame)[1]

    assert (condition.status, condition.driver, condition.diagnosis) == ("alert", "kurtosis", None)


def test_neighbour_with_the_same_fault_far_weaker_is_crosstalk():
    frame = run()
    fault(frame, 1, "env_bpfo", 38.0)
    fault(frame, 2, "env_bpfo", 5.0)

    result = latest(frame)

    assert result[1].status == "alert"
    assert (result[2].status, result[2].crosstalk_from, result[2].alert_at) == (
        "crosstalk",
        1,
        None,
    )
    # The fault it picks up, not one of its own.
    assert result[2].diagnosis == "outer race"


def test_neighbour_with_a_comparable_fault_alerts_too():
    frame = run()
    fault(frame, 1, "env_bpfo", 38.0)
    fault(frame, 2, "env_bpfo", 20.0)

    assert [c.status for c in latest(frame).values()] == ["alert", "alert"]


def test_different_faults_on_neighbours_both_alert():
    frame = run()
    fault(frame, 1, "env_bpfi", 38.0)
    fault(frame, 2, "env_bsf", 5.0)

    result = latest(frame)

    assert (result[1].status, result[1].diagnosis) == ("alert", PARTS["env_bpfi"])
    assert (result[2].status, result[2].diagnosis) == ("alert", PARTS["env_bsf"])


def test_crosstalk_turns_into_an_alert_once_the_bearing_has_a_fault_of_its_own():
    frame = run()
    fault(frame, 1, "env_bpfo", 38.0)
    fault(frame, 2, "env_bpfo", 5.0)
    fault(frame, 2, "env_bsf", 10.0, start=60)

    history = assess(frame)[1]

    assert history.at(hour(59)).status == "crosstalk"
    condition = history.conditions[-1]
    assert (condition.status, condition.diagnosis) == ("alert", "roller element")
    # Its own fault needs half an hour to dominate the medians, then a full hour to hold.
    assert hour(61) < condition.alert_at < hour(62)


def test_one_hour_where_the_source_looks_less_dominant_does_not_latch_an_alert():
    frame = run()
    fault(frame, 1, "env_bpfo", 38.0)
    fault(frame, 2, "env_bpfo", 5.0)
    frame.loc[between(frame, 60, 60.1) & (frame["bearing"] == 2), "env_bpfo"] = 30.0

    assert latest(frame)[2].status == "crosstalk"


def test_alert_turns_into_danger_once_the_energy_holds_above_threshold():
    frame = run()
    fault(frame, 1, "env_bpfo", 38.0)
    fault(frame, 1, "rms", 3.0, start=55)

    history = assess(frame)[0]

    assert history.at(hour(55.5)).status == "alert"
    condition = history.conditions[-1]
    assert (condition.status, condition.alert_at, condition.danger_at) == (
        "danger",
        hour(49),
        hour(56),
    )
    assert condition.diagnosis == "outer race"


def test_energy_rising_first_raises_alert_and_danger_at_once_without_a_part():
    frame = run()
    fault(frame, 1, "rms", 3.0)

    condition = latest(frame)[1]

    assert (condition.status, condition.diagnosis) == ("danger", None)
    assert condition.alert_at == condition.danger_at == hour(49)


def test_danger_latches_although_the_energy_drops_again():
    frame = run()
    fault(frame, 1, "env_bpfo", 38.0)
    fault(frame, 1, "rms", 3.0, start=50)
    frame.loc[between(frame, 52) & (frame["bearing"] == 1), "rms"] = 1.0

    condition = latest(frame)[1]

    assert (condition.status, condition.danger_at) == ("danger", hour(51))


def test_crosstalk_never_turns_into_danger_although_its_energy_rises():
    frame = run()
    fault(frame, 1, "env_bpfo", 38.0)
    fault(frame, 2, "env_bpfo", 5.0)
    fault(frame, 2, "rms", 3.0)

    condition = latest(frame)[2]

    assert (condition.status, condition.alert_at, condition.danger_at) == ("crosstalk", None, None)


def test_history_answers_as_of_any_time():
    history = assess(run())[0]
    first, second = history.conditions[:2]

    assert history.at(START - pd.Timedelta(seconds=1)) is None
    assert history.at(first.as_of) == first
    assert history.at(second.as_of - pd.Timedelta(seconds=1)) == first
    assert history.at() == history.conditions[-1]
    assert history.at(hour(1000)) == history.conditions[-1]


def test_every_condition_depends_only_on_the_data_up_to_it():
    """The replay guarantee: judging a run cut off at t gives the same condition at t."""
    rng = np.random.default_rng(0)
    frame = run(hours=40)
    frame[list(FEATURES)] = rng.lognormal(0, 0.1, size=(len(frame), len(FEATURES)))
    growth = ((frame["timestamp"] - hour(26)) / pd.Timedelta(hours=1)).clip(lower=0)
    frame.loc[frame["bearing"] == 1, "env_bpfo"] += 1.5 * growth
    frame.loc[frame["bearing"] == 2, "env_bpfo"] += 0.25 * growth
    frame.loc[frame["bearing"] == 1, "rms"] += 0.5 * (growth - 6).clip(lower=0)

    full: dict[int, History] = {h.bearing: h for h in assess(frame)}
    statuses = {c.status for h in full.values() for c in h.conditions}
    all_states = {"baseline", "ok", "crosstalk", "alert", "danger"}
    assert statuses == all_states, "scenario must cover all states"

    for cut in frame["timestamp"].drop_duplicates()[::6]:
        for history in assess(frame[frame["timestamp"] <= cut]):
            assert history.conditions[-1] == full[history.bearing].at(cut), cut


def machine(*statuses: str) -> tuple[str, list[int]]:
    """The machine's status and bearings, for bearings 1, 2, ... with these statuses."""
    conditions = {b: Condition(as_of=START, status=s) for b, s in enumerate(statuses, start=1)}
    result = machine_condition(conditions)
    return result.status, result.bearings


def test_machine_is_as_bad_as_its_worst_bearing():
    assert machine("ok", "alert", "danger", "danger") == ("danger", [3, 4])
    assert machine("ok", "alert", "crosstalk", "ok") == ("alert", [2])


def test_a_bearing_that_only_hears_a_neighbour_leaves_the_machine_ok():
    assert machine("ok", "crosstalk", "ok", "crosstalk") == ("ok", [])


def test_machine_is_not_ok_while_a_bearing_is_still_learning():
    assert machine("baseline", "ok") == ("baseline", [1])
    assert machine("baseline", "danger") == ("danger", [2])


def test_run_with_a_missing_bearing_row_is_rejected():
    with pytest.raises(ValueError, match="every bearing"):
        assess(run().iloc[:-1])
