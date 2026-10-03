import pandas as pd
import pytest

from monitor.features import FEATURES
from monitor.health import PARTS, alarm_time, bearing_ratios, health_index

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


def index_series(
    values: list[float], step: str = "10min", start: pd.Timestamp = START
) -> pd.Series:
    """One bearing's health index, one value per snapshot."""
    return pd.Series(values, index=pd.date_range(start, periods=len(values), freq=step))


def after(frame: pd.DataFrame, hours: float) -> pd.Series:
    return frame["timestamp"] >= START + pd.Timedelta(hours=hours)


def index_of(frame: pd.DataFrame, bearing: int) -> pd.DataFrame:
    return health_index(bearing_ratios(frame)).loc[bearing]


def test_steady_run_has_index_one_whatever_each_channel_measures():
    frame = run()
    frame.loc[frame["channel"] == 2, list(FEATURES)] = 40.0

    assert health_index(bearing_ratios(frame))["health_index"].tolist() == pytest.approx(
        [1.0] * len(frame)
    )


def test_baseline_is_the_first_24_hours_only():
    frame = run()
    frame.loc[~after(frame, 24), "rms"] = 0.5

    index = index_of(frame, 1)["health_index"]

    assert index[index.index < START + pd.Timedelta(hours=24)].eq(1.0).all()
    assert index[index.index >= START + pd.Timedelta(hours=24)].eq(2.0).all()


def test_one_spike_in_the_baseline_does_not_shift_it():
    frame = run()
    frame.loc[0, "kurtosis"] = 100.0

    assert index_of(frame, 1)["health_index"].iloc[-1] == 1.0


def test_index_names_the_feature_that_rose_and_its_part():
    frame = run()
    frame.loc[after(frame, 48) & (frame["bearing"] == 1), "env_bpfi"] = 3.0

    last = index_of(frame, 1).iloc[-1]

    assert last["health_index"] == 3.0
    assert PARTS[last["feature"]] == "inner race"
    assert index_of(frame, 2)["health_index"].eq(1.0).all()


def test_bearing_is_as_bad_as_its_worse_channel():
    frame = run(pairs=((1, 1), (1, 2)))
    frame.loc[after(frame, 48) & (frame["channel"] == 2), "env_bpfo"] = 4.0

    last = index_of(frame, 1).iloc[-1]

    assert (last["health_index"], last["feature"]) == (4.0, "env_bpfo")


def test_alarm_needs_the_index_above_threshold_for_a_full_hour():
    # 10-minute snapshots: six above span 50 minutes, the seventh completes the hour.
    assert alarm_time(index_series([1.0] + [2.5] * 6 + [1.0] * 5)) is None

    index = index_series([1.0] + [2.5] * 7 + [1.0] * 5)

    assert alarm_time(index) == index.index[7]


def test_dip_below_threshold_restarts_the_hour():
    assert alarm_time(index_series([2.5] * 4 + [1.9] + [2.5] * 4)) is None


def test_hold_is_measured_in_time_not_snapshots():
    # Seven snapshots at 5 minutes cover only half an hour.
    assert alarm_time(index_series([2.5] * 7, step="5min")) is None
    assert alarm_time(index_series([2.5] * 13, step="5min")) is not None


def test_gap_in_the_recording_restarts_the_hour():
    before_pause = index_series([2.5] * 4)
    after_pause = index_series([2.5] * 4, start=START + pd.Timedelta(days=6))

    assert alarm_time(pd.concat([before_pause, after_pause])) is None


def test_alarm_latches_although_the_index_drops_again():
    index = index_series([2.5] * 7 + [1.0] * 20)

    assert alarm_time(index) == index.index[6]
