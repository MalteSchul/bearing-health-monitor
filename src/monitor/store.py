"""The feature table the API serves, loaded once at startup and shaped into responses."""

from dataclasses import fields
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from pydantic import BaseModel

from monitor.features import ChannelFeatures
from monitor.ims import EXPERIMENTS

FEATURES = tuple(f.name for f in fields(ChannelFeatures))
FAILURES = {e.name: e.failures for e in EXPERIMENTS}


class BearingSummary(BaseModel):
    bearing: int
    channels: list[int]
    # Documented state at the end of the run, None if the bearing survived. For display only:
    # the detector must never see it.
    failure: str | None


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


class FeatureStore:
    def __init__(self, frame: pd.DataFrame) -> None:
        frame = frame.sort_values(["experiment", "channel", "timestamp"])
        self._experiments: dict[str, ExperimentSummary] = {}
        self._bearings: dict[tuple[str, int], BearingFeatures] = {}
        for experiment_key, runs in frame.groupby("experiment", observed=True):
            experiment = str(experiment_key)
            failures = FAILURES.get(experiment, {})
            summaries = []
            for bearing in sorted(runs["bearing"].unique().tolist()):
                rows = runs[runs["bearing"] == bearing]
                channels = [
                    _channel_series(channel, rows[rows["channel"] == channel])
                    for channel in sorted(rows["channel"].unique().tolist())
                ]
                failure = failures.get(bearing)
                self._bearings[(experiment, bearing)] = BearingFeatures(
                    experiment=experiment, bearing=bearing, failure=failure, channels=channels
                )
                summaries.append(
                    BearingSummary(
                        bearing=bearing, channels=[c.channel for c in channels], failure=failure
                    )
                )
            self._experiments[experiment] = ExperimentSummary(
                name=experiment,
                first=runs["timestamp"].min(),
                last=runs["timestamp"].max(),
                snapshots=runs["timestamp"].nunique(),
                bearings=summaries,
            )

    @classmethod
    def load(cls, path: Path) -> "FeatureStore":
        return cls(pd.read_parquet(path))

    def experiments(self) -> list[ExperimentSummary]:
        return list(self._experiments.values())

    def experiment(self, name: str) -> ExperimentSummary | None:
        return self._experiments.get(name)

    def bearing(self, experiment: str, bearing: int) -> BearingFeatures | None:
        return self._bearings.get((experiment, bearing))
