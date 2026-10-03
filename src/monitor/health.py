"""Health of each bearing in a run, judged against that run's own healthy start.

Every function takes the feature rows of one run: timestamp, bearing, channel and FEATURES.
"""

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
