"""Compute condition features for every IMS snapshot into data/processed/features.parquet.

    uv run python scripts/extract_features.py

Needs the raw data from scripts/ingest_ims.py. Writes one row per snapshot and channel. The app
serves this file, so it is committed; rerun after changing monitor.features.
"""

import sys
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict
from functools import partial
from pathlib import Path

import pandas as pd

from monitor.config import PROJECT_ROOT
from monitor.features import channel_features
from monitor.ims import EXPERIMENTS, Experiment, read_snapshot, snapshot_time

DATASET_DIR = PROJECT_ROOT / "data" / "raw" / "ims"
OUTPUT = PROJECT_ROOT / "data" / "processed" / "features.parquet"


def snapshot_rows(path: Path, bearings: tuple[int, ...]) -> list[dict[str, object]]:
    samples = read_snapshot(path)
    timestamp = snapshot_time(path.name)
    return [
        {
            "timestamp": timestamp,
            "bearing": bearing,
            "channel": column + 1,
            **asdict(channel_features(samples[:, column])),
        }
        for column, bearing in enumerate(bearings)
    ]


def extract(experiment: Experiment, pool: ProcessPoolExecutor) -> pd.DataFrame:
    paths = [
        path
        for path in sorted((DATASET_DIR / experiment.name).iterdir())
        if path.name not in experiment.silent_snapshots
    ]
    rows_per_snapshot = pool.map(
        partial(snapshot_rows, bearings=experiment.bearings), paths, chunksize=32
    )
    frame = pd.DataFrame([row for rows in rows_per_snapshot for row in rows])
    frame.insert(0, "experiment", experiment.name)
    print(f"{experiment.name}: {len(paths)} snapshots, {len(frame)} rows")
    return frame


def main() -> None:
    if not DATASET_DIR.is_dir():
        sys.exit(f"{DATASET_DIR} not found. Download the data first: just data")
    start = time.perf_counter()
    with ProcessPoolExecutor() as pool:
        features = pd.concat([extract(e, pool) for e in EXPERIMENTS], ignore_index=True)

    features["experiment"] = features["experiment"].astype("category")
    features[["bearing", "channel"]] = features[["bearing", "channel"]].astype("int8")
    # float32 keeps 7 significant digits, far more than the 3-decimal raw samples; halves the file.
    float_columns = features.select_dtypes("float64").columns
    features[float_columns] = features[float_columns].astype("float32")

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    features.to_parquet(OUTPUT, index=False)
    size_mb = OUTPUT.stat().st_size / 1e6
    elapsed = time.perf_counter() - start
    print(f"Wrote {OUTPUT.relative_to(PROJECT_ROOT)}: {len(features)} rows, {size_mb:.1f} MB")
    print(f"Took {elapsed:.0f} s")


if __name__ == "__main__":
    main()
