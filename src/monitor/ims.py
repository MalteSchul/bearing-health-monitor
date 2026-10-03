"""The NASA IMS bearing run-to-failure dataset: what the files are and how to check them.

Each experiment is a directory of snapshots: 1-second vibration recordings at 20 kHz named after
their start time, one tab-separated column per accelerometer channel. Facts below are from the
readme shipped with the data, corrected where the files disagree with it.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
import numpy.typing as npt
import pandas as pd

SAMPLES_PER_SNAPSHOT = 20_480
TIMESTAMP_FORMAT = "%Y.%m.%d.%H.%M.%S"
# Below this RMS on every channel a snapshot holds no vibration, only sensor noise. Healthy
# snapshots sit around 0.05-0.15 V, silent ones around 0.002 V.
SILENT_RMS_V = 0.01


@dataclass(frozen=True)
class Experiment:
    name: str
    # Bearing measured by each column, in column order.
    bearings: tuple[int, ...]
    n_snapshots: int
    first: datetime
    last: datetime
    # Snapshots recorded with all channels silent, presumably after the rig stopped.
    silent_snapshots: frozenset[str] = frozenset()


EXPERIMENTS = (
    # Failed: bearing 3 inner race, bearing 4 roller element. Two accelerometers (x, y) per bearing.
    Experiment(
        name="set1",
        bearings=(1, 1, 2, 2, 3, 3, 4, 4),
        n_snapshots=2_156,
        first=datetime(2003, 10, 22, 12, 6, 24),
        last=datetime(2003, 11, 25, 23, 39, 56),
    ),
    # Failed: bearing 1 outer race.
    Experiment(
        name="set2",
        bearings=(1, 2, 3, 4),
        n_snapshots=984,
        first=datetime(2004, 2, 12, 10, 32, 39),
        last=datetime(2004, 2, 19, 6, 22, 39),
        silent_snapshots=frozenset({"2004.02.19.06.12.39", "2004.02.19.06.22.39"}),
    ),
    # Failed: bearing 3 outer race. The readme says 4,448 snapshots ending 2004-04-04 19:01:57,
    # but the archive holds 1,876 more, and the degradation of bearing 3 is in those.
    Experiment(
        name="set3",
        bearings=(1, 2, 3, 4),
        n_snapshots=6_324,
        first=datetime(2004, 3, 4, 9, 27, 46),
        last=datetime(2004, 4, 18, 2, 42, 55),
        silent_snapshots=frozenset({"2004.04.18.02.42.55"}),
    ),
)


@dataclass(frozen=True)
class SnapshotSummary:
    name: str
    shape: tuple[int, int] = (0, 0)
    rms: tuple[float, ...] = ()
    finite: bool = True
    error: str | None = None


def snapshot_time(name: str) -> datetime:
    return datetime.strptime(name, TIMESTAMP_FORMAT)


def read_snapshot(path: Path) -> npt.NDArray[np.float64]:
    """Samples of one snapshot, shape (samples, channels), in volts."""
    frame = pd.read_csv(path, sep="\t", header=None, dtype=np.float64)
    return frame.to_numpy()


def summarize_snapshot(path: Path) -> SnapshotSummary:
    try:
        samples = read_snapshot(path)
    except (ValueError, pd.errors.ParserError) as exc:
        return SnapshotSummary(name=path.name, error=f"unreadable: {exc}")
    rows, cols = samples.shape
    rms = np.sqrt(np.mean(samples**2, axis=0))
    return SnapshotSummary(
        name=path.name,
        shape=(rows, cols),
        rms=tuple(float(x) for x in rms),
        finite=bool(np.isfinite(samples).all()),
    )


def validate_experiment(experiment: Experiment, summaries: Sequence[SnapshotSummary]) -> list[str]:
    """Problems found in an experiment's snapshots, empty if it matches what we expect."""
    problems = []
    if len(summaries) != experiment.n_snapshots:
        problems.append(f"{len(summaries)} snapshots, expected {experiment.n_snapshots}")

    try:
        times = sorted(snapshot_time(s.name) for s in summaries)
    except ValueError as exc:
        return [*problems, f"snapshot name is not a timestamp: {exc}"]
    if times and (times[0], times[-1]) != (experiment.first, experiment.last):
        problems.append(
            f"snapshots span {times[0]} to {times[-1]}, "
            f"expected {experiment.first} to {experiment.last}"
        )

    expected_shape = (SAMPLES_PER_SNAPSHOT, len(experiment.bearings))
    silent = set()
    for s in summaries:
        if s.error:
            problems.append(f"{s.name}: {s.error}")
        elif s.shape != expected_shape:
            problems.append(f"{s.name}: shape {s.shape}, expected {expected_shape}")
        elif not s.finite:
            problems.append(f"{s.name}: contains NaN or infinite samples")
        elif max(s.rms) < SILENT_RMS_V:
            silent.add(s.name)
    if silent != experiment.silent_snapshots:
        problems.append(
            f"silent snapshots {sorted(silent)}, expected {sorted(experiment.silent_snapshots)}"
        )
    return problems
