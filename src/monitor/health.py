"""Condition of each bearing in a run, judged against that run's own healthy start.

A run is the feature rows of one experiment: timestamp, bearing, channel and FEATURES. Each
snapshot is judged only from the snapshots up to it, so a replay shows what an operator would
have seen at the time.
"""

from bisect import bisect_right
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Literal

import numpy as np
import numpy.typing as npt
import pandas as pd
from pydantic import Field

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
# In set 2 the failing bearing showed its fault a median 7 x stronger than the loudest neighbour.
CROSSTALK_FACTOR = 3.0
# Danger also needs this to hold at THRESHOLD: a spreading defect raises the vibration energy, a
# new one hardly does. Chosen after all runs had been seen, but it adds no new number.
ENERGY = "rms"

# The part whose impact rate each envelope feature measures. Time-domain features rise for any
# fault, so they say that something changed but not where.
PARTS = {
    "env_bpfo": "outer race",
    "env_bpfi": "inner race",
    "env_bsf": "roller element",
    "env_ftf": "cage",
}
ENVELOPE = [FEATURES.index(name) for name in PARTS]

# alert: plan the replacement. danger: act now. crosstalk needs no action on this bearing.
Status = Literal["baseline", "ok", "crosstalk", "alert", "danger"]

# Without an example, Swagger UI invents the current time in UTC with a "Z".
Timestamp = Annotated[
    datetime,
    Field(
        description="Local time at the test rig. The dataset recorded no offset, so none is given.",
        examples=["2004-02-16T04:00:00"],
    ),
]


@dataclass(frozen=True)
class Condition:
    """What the monitor says about one bearing, judged from the snapshots up to `as_of`."""

    as_of: Timestamp
    status: Status
    # The largest feature ratio and the feature it comes from; None during the baseline.
    index: float | None = None
    driver: str | None = None
    # Not for baseline and ok: the part whose fault frequency stood out over the last hour.
    diagnosis: str | None = None
    alert_at: Timestamp | None = None
    danger_at: Timestamp | None = None
    # For crosstalk: the bearing whose fault this one picks up.
    crosstalk_from: int | None = None


@dataclass(frozen=True)
class History:
    """One bearing's condition at every snapshot of its run, in time order."""

    bearing: int
    conditions: tuple[Condition, ...]

    def at(self, time: datetime | None = None) -> Condition | None:
        """The condition as of `time`, the latest without one; None before the first snapshot."""
        if time is None:
            return self.conditions[-1]
        i = bisect_right(self.conditions, time, key=lambda c: c.as_of)
        return self.conditions[i - 1] if i else None


# A bearing that only hears a neighbour needs no action, so crosstalk counts as ok here.
MachineStatus = Literal["baseline", "ok", "alert", "danger"]
# Most urgent first. A known danger outranks a bearing still learning; not knowing outranks ok.
ESCALATION: tuple[MachineStatus, ...] = ("danger", "alert", "baseline")


@dataclass(frozen=True)
class MachineCondition:
    status: MachineStatus
    # The bearings at that status; none when the machine is ok.
    bearings: list[int]


def machine_condition(conditions: dict[int, Condition]) -> MachineCondition:
    """The machine is as bad as its worst bearing: it is stopped and repaired as a whole, and an
    average would let healthy bearings hide a failing one.
    """
    for status in ESCALATION:
        bearings = sorted(b for b, c in conditions.items() if c.status == status)
        if bearings:
            return MachineCondition(status=status, bearings=bearings)
    return MachineCondition(status="ok", bearings=[])


def bearing_ratios(run: pd.DataFrame) -> pd.DataFrame:
    """Each feature as a multiple of its baseline median, indexed by (bearing, timestamp).

    NaN during the baseline itself: until it is complete there is nothing to compare against.
    """
    features = list(FEATURES)
    judged = run["timestamp"] >= run["timestamp"].min() + BASELINE
    # Median, not mean: one spike in the baseline must not shift it.
    baseline = run[~judged].groupby("channel")[features].median()
    # In float64: the table stores float32, whose rounding would show as 3.9119999 in the API.
    ratios = run[features].astype(np.float64) / baseline.loc[run["channel"]].to_numpy()
    ratios.loc[~judged] = np.nan
    ratios[["bearing", "timestamp"]] = run[["bearing", "timestamp"]]
    # A defect shows most clearly on the sensor nearest to it, so a bearing is as bad as its
    # worse channel.
    worst: pd.DataFrame = ratios.groupby(["bearing", "timestamp"]).max()
    return worst


def health_index(ratios: pd.DataFrame) -> pd.DataFrame:
    """The largest ratio per bearing and snapshot, and the feature it comes from."""
    judged = ratios.notna().any(axis=1)
    return pd.DataFrame(
        {
            # Max, not mean: a fault raises one feature first, and the other seven would dilute
            # it. Three decimals, so decisions use exactly the value that is reported.
            "health_index": ratios.max(axis=1).round(3),
            # idxmax refuses rows without values; those are the baseline, which has no index.
            "feature": ratios.fillna(-np.inf).idxmax(axis=1).where(judged),
        }
    )


def held(index: pd.Series) -> pd.Series:
    """For each snapshot, whether one bearing's index has stayed at THRESHOLD or above for HOLD."""
    since: pd.Timestamp | None = None
    previous: pd.Timestamp | None = None
    holding = []
    for timestamp, value in zip(pd.DatetimeIndex(index.index), index, strict=True):
        if not value >= THRESHOLD:  # NaN during the baseline, too
            since = None
        elif since is None or previous is None or timestamp - previous > MAX_GAP:
            since = timestamp
        holding.append(since is not None and timestamp - since >= HOLD)
        previous = timestamp
    return pd.Series(holding, index=index.index)


def loudest_neighbour(recent: npt.NDArray[np.float64], bearing: int) -> tuple[int, float]:
    """Position of the other bearing that showed this one's strongest feature loudest, and how
    many times louder. `recent` holds each bearing's median ratio per feature over the last hour.
    """
    feature = int(recent[bearing].argmax())
    loudness = recent[:, feature].copy()
    own = loudness[bearing]
    loudness[bearing] = -np.inf
    loudest = int(loudness.argmax())
    return loudest, float(loudness[loudest] / own)


def diagnose(recent: npt.NDArray[np.float64]) -> str | None:
    """The part whose envelope feature stood out most in one bearing's medians, if any did."""
    strongest = max(ENVELOPE, key=lambda k: recent[k])
    return PARTS[FEATURES[strongest]] if recent[strongest] >= THRESHOLD else None


def assess(run: pd.DataFrame) -> list[History]:
    """Every bearing's condition at every snapshot of one run."""
    ratios = bearing_ratios(run)
    index = health_index(ratios)
    bearings = ratios.index.unique("bearing").tolist()
    times = ratios.index.unique("timestamp")
    if len(ratios) != len(bearings) * len(times):
        raise ValueError("every snapshot needs features for every bearing")
    # Medians over the last hour, so a single noisy snapshot cannot decide crosstalk or the part.
    recent = np.stack([ratios.loc[b].rolling(HOLD).median().to_numpy() for b in bearings], axis=1)

    histories = []
    for position, bearing in enumerate(bearings):
        own = index.loc[bearing]
        values = own["health_index"]
        explained = [
            value >= THRESHOLD and loudest_neighbour(recent[i], position)[1] >= CROSSTALK_FACTOR
            for i, value in enumerate(values)
        ]
        elevated = held(values).tolist()
        # An alert needs an hour of the bearing's own evidence: snapshots that a louder neighbour
        # explains do not count. One noisy hour must not latch an alert for good.
        alerting = held(values.mask(explained)).tolist()
        # Danger waits for the alert: energy reaches the neighbours almost undiminished (the
        # source only 1.3 x louder in set 2), so only the alert's crosstalk check can place it.
        energy = held(ratios.loc[bearing][ENERGY].round(3)).tolist()

        alert_at: datetime | None = None
        danger_at: datetime | None = None
        conditions = []
        for i, (time, value, driver) in enumerate(
            zip(own.index.to_pydatetime(), values, own["feature"], strict=True)
        ):
            if np.isnan(value):
                conditions.append(Condition(as_of=time, status="baseline"))
                continue
            if alert_at is None and alerting[i]:
                alert_at = time
            if danger_at is None and alert_at is not None and energy[i]:
                danger_at = time
            crosstalk = alert_at is None and elevated[i]
            status: Status = "crosstalk" if crosstalk else "ok"
            if danger_at is not None:
                status = "danger"
            elif alert_at is not None:
                status = "alert"
            source = loudest_neighbour(recent[i], position)[0] if crosstalk else None
            conditions.append(
                Condition(
                    as_of=time,
                    status=status,
                    index=float(value),
                    driver=str(driver),
                    diagnosis=None if status == "ok" else diagnose(recent[i, position]),
                    alert_at=alert_at,
                    danger_at=danger_at,
                    crosstalk_from=None if source is None else int(bearings[source]),
                )
            )
        histories.append(History(bearing=int(bearing), conditions=tuple(conditions)))
    return histories
