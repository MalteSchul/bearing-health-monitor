"""Health of each bearing in a run, judged against that run's own healthy start.

A run is the feature rows of one experiment: timestamp, bearing, channel and FEATURES.
"""

from dataclasses import dataclass
from typing import Literal

import pandas as pd

from monitor.features import FEATURES

# Fixed in advance rather than picked by eye, so no knowledge of the failure leaks in.
BASELINE = pd.Timedelta(hours=24)
# Healthy snapshots of set 2 reach 1.55 x at the 99th percentile; single ones up to 2.07 x.
THRESHOLD = 2.0
# A time, not a snapshot count: the recording interval varies (5 and 10 min in set 1).
HOLD = pd.Timedelta(hours=1)
# Longer without data restarts the hold: a gap is no evidence that the index stayed high.
MAX_GAP = pd.Timedelta(minutes=30)
# All bearings share one shaft and housing, so a fault reaches the other sensors too, weaker.
# In set 2 the failing bearing showed its fault 7 x stronger than the loudest neighbour.
CROSSTALK_FACTOR = 3.0

# The part whose impact rate each envelope feature measures. Time-domain features rise for any
# fault, so they say that something changed but not where.
PARTS = {
    "env_bpfo": "outer race",
    "env_bpfi": "inner race",
    "env_bsf": "roller element",
    "env_ftf": "cage",
}


def bearing_ratios(run: pd.DataFrame) -> pd.DataFrame:
    """Each feature as a multiple of its baseline median, indexed by (bearing, timestamp)."""
    features = list(FEATURES)
    healthy = run[run["timestamp"] < run["timestamp"].min() + BASELINE]
    # Median, not mean: one spike in the baseline must not shift it.
    baseline = healthy.groupby("channel")[features].median()
    ratios = run[features] / baseline.loc[run["channel"]].to_numpy()
    ratios[["bearing", "timestamp"]] = run[["bearing", "timestamp"]]
    # A defect shows most clearly on the sensor nearest to it, so a bearing is as bad as its
    # worse channel.
    worst: pd.DataFrame = ratios.groupby(["bearing", "timestamp"]).max()
    return worst


def health_index(ratios: pd.DataFrame) -> pd.DataFrame:
    """The largest ratio per bearing and snapshot, and the feature it comes from."""
    # Max, not mean: a fault raises one feature first, and the other seven would dilute it.
    return pd.DataFrame({"health_index": ratios.max(axis=1), "feature": ratios.idxmax(axis=1)})


def alarm_time(index: pd.Series) -> pd.Timestamp | None:
    """When one bearing's index, indexed by timestamp, has first held THRESHOLD for HOLD.

    The alarm latches: a damaged bearing does not heal, so later dips do not clear it.
    """
    start: pd.Timestamp | None = None
    previous: pd.Timestamp | None = None
    index = index.sort_index()
    for timestamp, value in zip(pd.DatetimeIndex(index.index), index, strict=True):
        if value < THRESHOLD:
            start = None
        elif start is None or previous is None or timestamp - previous > MAX_GAP:
            start = timestamp
        if start is not None and timestamp - start >= HOLD:
            return timestamp
        previous = timestamp
    return None


@dataclass(frozen=True)
class Assessment:
    bearing: int
    status: Literal["ok", "alarm", "crosstalk"]
    alarm_time: pd.Timestamp | None = None
    # The feature that drove the index most often in the hour before the alarm.
    feature: str | None = None
    part: str | None = None
    # For crosstalk: the bearing whose fault this one picks up.
    source: int | None = None


def _hold_window(frame: pd.DataFrame, at: pd.Timestamp) -> pd.DataFrame:
    """Rows of a timestamp-indexed frame in the hour that raised an alarm at `at`."""
    return frame[(frame.index > at - HOLD) & (frame.index <= at)]


def crosstalk_source(
    ratios: pd.DataFrame, bearing: int, feature: str, at: pd.Timestamp
) -> int | None:
    """Another bearing that showed `feature` CROSSTALK_FACTOR x stronger in the alarm's hour."""
    by_bearing = _hold_window(ratios[feature].unstack("bearing"), at).median()
    stronger = by_bearing.drop(bearing)
    stronger = stronger[stronger >= CROSSTALK_FACTOR * by_bearing[bearing]]
    return int(stronger.idxmax()) if len(stronger) else None


def assess(run: pd.DataFrame) -> list[Assessment]:
    ratios = bearing_ratios(run)
    index = health_index(ratios)
    assessments = []
    for bearing in index.index.unique("bearing").tolist():
        at = alarm_time(index.loc[bearing, "health_index"])
        if at is None:
            assessments.append(Assessment(bearing=bearing, status="ok"))
            continue
        feature = str(_hold_window(index.loc[bearing], at)["feature"].mode()[0])
        source = crosstalk_source(ratios, bearing, feature, at)
        assessments.append(
            Assessment(
                bearing=bearing,
                status="alarm" if source is None else "crosstalk",
                alarm_time=at,
                feature=feature,
                part=PARTS.get(feature),
                source=source,
            )
        )
    return assessments
