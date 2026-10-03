import pandas as pd
import pytest

from monitor.features import FEATURES
from monitor.health import PARTS, bearing_ratios, health_index

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
