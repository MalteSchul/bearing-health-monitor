"""The feature table the API serves, loaded once at startup and shaped into responses."""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from pydantic import BaseModel

from monitor.features import FEATURES
from monitor.health import THRESHOLD, Condition, History, Status, assess
from monitor.ims import EXPERIMENTS

FAILURES = {e.name: e.failures for e in EXPERIMENTS}


class BearingSummary(BaseModel):
    bearing: int
    channels: list[int]
    # Documented state at the end of the run, None if the bearing survived. For display only:
    # the detector must never see it.
    failure: str | None
    condition: Condition


class ExperimentSummary(BaseModel):
    name: str
    first: datetime
    last: datetime
    snapshots: int
    bearings: list[BearingSummary]


class ChannelSeries(BaseModel):
    """One array per feature, aligned with timestamps: the shape Plotly traces take."""

    channel: int
    timestamps: list[datetime]
    rms: list[float]
    peak: list[float]
    crest_factor: list[float]
    kurtosis: list[float]
    env_ftf: list[float]
    env_bsf: list[float]
    env_bpfo: list[float]
    env_bpfi: list[float]


class BearingFeatures(BaseModel):
    experiment: str
    bearing: int
    failure: str | None
    channels: list[ChannelSeries]


class HealthIndex(BaseModel):
    """The index at every snapshot, the feature behind it and the status it led to."""

    experiment: str
    bearing: int
    threshold: float
    timestamps: list[datetime]
    # None while the baseline is being recorded.
    index: list[float | None]
    driver: list[str | None]
    status: list[Status]


class NoDataYet(LookupError):
    """A condition was asked for before the first snapshot of its run."""


@dataclass(frozen=True)
class _Bearing:
    channels: list[int]
    failure: str | None
    history: History


@dataclass(frozen=True)
class _Run:
    first: datetime
    last: datetime
    snapshots: int
    bearings: dict[int, _Bearing]


def _short_floats(values: pd.Series) -> list[float]:
    # Widened to float64, a float32 prints 17 digits. Its shortest float32 text has about 8 and
    # parses back to the same value, which halves the JSON.
    return list(values.to_numpy(np.float32).astype(str).astype(np.float64).tolist())


def _channel_series(channel: int, rows: pd.DataFrame) -> ChannelSeries:
    return ChannelSeries(
        channel=channel,
        timestamps=rows["timestamp"].tolist(),
        **{name: _short_floats(rows[name]) for name in FEATURES},
    )


def _health_index(experiment: str, history: History) -> HealthIndex:
    conditions = history.conditions
    return HealthIndex(
        experiment=experiment,
        bearing=history.bearing,
        threshold=THRESHOLD,
        timestamps=[c.as_of for c in conditions],
        index=[c.index for c in conditions],
        driver=[c.driver for c in conditions],
        status=[c.status for c in conditions],
    )


def _bearing_summary(bearing: int, data: _Bearing, at: datetime | None) -> BearingSummary:
    condition = data.history.at(at)
    if condition is None:
        first = data.history.conditions[0].as_of
        raise NoDataYet(f"No condition before the first snapshot at {first.isoformat()}")
    return BearingSummary(
        bearing=bearing, channels=data.channels, failure=data.failure, condition=condition
    )


def _experiment_summary(name: str, run: _Run, at: datetime | None = None) -> ExperimentSummary:
    return ExperimentSummary(
        name=name,
        first=run.first,
        last=run.last,
        snapshots=run.snapshots,
        bearings=[_bearing_summary(bearing, data, at) for bearing, data in run.bearings.items()],
    )


class FeatureStore:
    def __init__(self, frame: pd.DataFrame) -> None:
        frame = frame.sort_values(["experiment", "channel", "timestamp"])
        self._runs: dict[str, _Run] = {}
        self._features: dict[tuple[str, int], BearingFeatures] = {}
        self._health: dict[tuple[str, int], HealthIndex] = {}
        for experiment_key, run in frame.groupby("experiment", observed=True):
            experiment = str(experiment_key)
            failures = FAILURES.get(experiment, {})
            # Judged once at startup: every condition depends only on data up to its snapshot,
            # so precomputing them all is the same as judging each snapshot live.
            histories = {h.bearing: h for h in assess(run)}
            bearings = {}
            for bearing in sorted(run["bearing"].unique().tolist()):
                rows = run[run["bearing"] == bearing]
                channels = [
                    _channel_series(channel, rows[rows["channel"] == channel])
                    for channel in sorted(rows["channel"].unique().tolist())
                ]
                failure = failures.get(bearing)
                self._features[(experiment, bearing)] = BearingFeatures(
                    experiment=experiment, bearing=bearing, failure=failure, channels=channels
                )
                self._health[(experiment, bearing)] = _health_index(experiment, histories[bearing])
                bearings[bearing] = _Bearing(
                    channels=[c.channel for c in channels],
                    failure=failure,
                    history=histories[bearing],
                )
            self._runs[experiment] = _Run(
                first=run["timestamp"].min(),
                last=run["timestamp"].max(),
                snapshots=run["timestamp"].nunique(),
                bearings=bearings,
            )

    @classmethod
    def load(cls, path: Path) -> "FeatureStore":
        return cls(pd.read_parquet(path))

    def experiments(self) -> list[ExperimentSummary]:
        return [_experiment_summary(name, run) for name, run in self._runs.items()]

    def experiment(self, name: str, at: datetime | None = None) -> ExperimentSummary | None:
        """The experiment with each bearing's condition as of `at`, the latest without one."""
        run = self._runs.get(name)
        return None if run is None else _experiment_summary(name, run, at)

    def bearing(
        self, experiment: str, bearing: int, at: datetime | None = None
    ) -> BearingSummary | None:
        run = self._runs.get(experiment)
        data = None if run is None else run.bearings.get(bearing)
        return None if data is None else _bearing_summary(bearing, data, at)

    def features(self, experiment: str, bearing: int) -> BearingFeatures | None:
        return self._features.get((experiment, bearing))

    def health_index(self, experiment: str, bearing: int) -> HealthIndex | None:
        return self._health.get((experiment, bearing))
