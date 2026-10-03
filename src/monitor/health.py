"""Health of each bearing in a run, judged against that run's own healthy start.

Every function takes the feature rows of one run: timestamp, bearing, channel and FEATURES.
"""

import pandas as pd

from monitor.features import FEATURES

# Fixed in advance rather than picked by eye, so no knowledge of the failure leaks in.
BASELINE = pd.Timedelta(hours=24)

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
