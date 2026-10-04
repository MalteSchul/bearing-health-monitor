"""Evaluate the detector on every run: warning time, diagnosis and false alarms per bearing.

    uv run python scripts/evaluate_detector.py

Reads the feature table the app serves. Thresholds were chosen on set 2 only; sets 1 and 3 are
evaluated unchanged, so their numbers are the honest ones. The danger level and the margin a part
needs before it is named came after all runs had been seen; they add no new number, but they are
not held out. The same detector is also run on
feature subsets: rms alone is the usual overall-level alarm, the others show what each domain adds.
"""

from datetime import datetime

import pandas as pd
from scipy import stats

from monitor.config import Settings
from monitor.evaluation import operating_hours, verdict
from monitor.features import FEATURES
from monitor.health import BASELINE, History, assess
from monitor.ims import EXPERIMENTS

TUNED_ON = {"set2"}
ENVELOPE = [name for name in FEATURES if name.startswith("env_")]
VARIANTS = {
    "all 8 (detector)": list(FEATURES),
    "envelope only": ENVELOPE,
    "time domain only": [name for name in FEATURES if name not in ENVELOPE],
    "rms only": ["rms"],
}


def hours(since: datetime, time: datetime | None) -> float | None:
    return None if time is None else round((time - since).total_seconds() / 3600, 1)


def restrict(run: pd.DataFrame, keep: list[str]) -> pd.DataFrame:
    """The run with every other feature pinned to a constant, whose ratio never leaves 1."""
    pinned = run.copy()
    pinned[[name for name in FEATURES if name not in keep]] = 1.0
    return pinned


def evaluate(
    history: History, start: datetime, times: pd.Series, failure: str | None
) -> dict[str, object]:
    end = history.conditions[-1]
    alert, danger = end.alert_at, end.danger_at
    at_alert = None if alert is None else history.at(alert)
    crosstalk = next((c.as_of for c in history.conditions if c.status == "crosstalk"), None)
    return {
        "bearing": history.bearing,
        "failure": failure or "-",
        "status": end.status,
        "alert_h": hours(start, alert),
        "danger_h": hours(start, danger),
        "end_h": hours(start, end.as_of),
        "warning_h": hours(alert, end.as_of) if alert else None,
        # The rig stood still for days in set 1; damage only grows while it runs.
        "warning_op_h": operating_hours(times, alert, end.as_of) if alert else None,
        "danger_op_h": operating_hours(times, danger, end.as_of) if danger else None,
        "diagnosis_at_alert": at_alert.diagnosis if at_alert else None,
        "diagnosis_at_end": end.diagnosis,
        "first_crosstalk_h": hours(start, crosstalk),
        "verdict": verdict(alert is not None, failure),
        # Survivor hours a false alarm could have happened in.
        "monitored_op_h": operating_hours(times, start + BASELINE, end.as_of),
    }


def results(features: pd.DataFrame, keep: list[str]) -> pd.DataFrame:
    rows = []
    for experiment in EXPERIMENTS:
        run = restrict(features[features["experiment"] == experiment.name], keep)
        start = run["timestamp"].min().to_pydatetime()
        times = run["timestamp"].drop_duplicates()
        for history in assess(run):
            row = evaluate(history, start, times, experiment.failures.get(history.bearing))
            role = "tune" if experiment.name in TUNED_ON else "test"
            rows.append({"run": experiment.name, "role": role, **row})
    return pd.DataFrame(rows)


def print_table(rows: list[dict[str, object]]) -> None:
    """One header line, text left and numbers right: pandas splits the header of a row index."""
    columns = list(rows[0])
    cells = [[str(row[c]) for c in columns] for row in rows]
    widths = [max(len(c), *(len(r[i]) for r in cells)) for i, c in enumerate(columns)]
    numeric = [all(isinstance(row[c], int | float) for row in rows) for c in columns]

    def line(values: list[str]) -> str:
        padded = (
            v.rjust(w) if n else v.ljust(w) for v, w, n in zip(values, widths, numeric, strict=True)
        )
        return "  ".join(padded).rstrip()

    print(line(columns))
    print(line(["-" * w for w in widths]))
    for row in cells:
        print(line(row))


def rate(hits: int, total: int) -> str:
    # Exact (Clopper-Pearson) interval: with 4 failures and 8 survivors, the rates are anecdotes.
    low, high = stats.binomtest(hits, total).proportion_ci(confidence_level=0.95, method="exact")
    return f"{hits}/{total} (95% CI {low:.0%}-{high:.0%})"


def summary(table: pd.DataFrame, warning: str = "warning_op_h") -> dict[str, object]:
    """Detections and false alarms of one level, read from the column with its warning time."""
    failed = table[table["failure"] != "-"]
    survivors = table[table["failure"] == "-"]
    detected = failed[warning].dropna()
    false_alarms = int(survivors[warning].notna().sum())
    return {
        "detected": rate(len(detected), len(failed)),
        "median warning": round(float(detected.median()), 1),
        "false alarms": rate(false_alarms, len(survivors)),
        "per 1000 h": round(false_alarms / survivors["monitored_op_h"].sum() * 1000, 2),
    }


def parts(table: pd.DataFrame) -> str:
    """Right, wrong and no part named at the end: naming none is no answer, a wrong one misleads."""
    failed = table[table["failure"] != "-"]
    named = int(failed["diagnosis_at_end"].notna().sum())
    right = int((failed["diagnosis_at_end"] == failed["failure"]).sum())
    return f"{right} / {named - right} / {len(failed) - named}"


def main() -> None:
    features = pd.read_parquet(Settings().features_path)
    pd.set_option("display.width", 250)

    tables = {name: results(features, keep) for name, keep in VARIANTS.items()}
    detector = tables["all 8 (detector)"]
    print(detector.drop(columns="monitored_op_h").to_string(index=False, na_rep="-"))

    print("\nWarning in operating hours before the run ended, per failure and feature set:\n")
    failed = detector[detector["failure"] != "-"]
    rows: list[dict[str, object]] = [
        {"run": run, "bearing": bearing, "failure": failure}
        for run, bearing, failure in failed[["run", "bearing", "failure"]].itertuples(index=False)
    ]
    for name, table in tables.items():
        warning = dict(
            zip(
                zip(table["run"], table["bearing"], strict=True), table["warning_op_h"], strict=True
            )
        )
        for row in rows:
            value = warning[(row["run"], row["bearing"])]
            row[name] = "missed" if pd.isna(value) else float(value)
    print_table(rows)

    print("\nSummary per feature set (same alert rule, same thresholds):\n")
    print_table(
        [
            {"feature set": name, **summary(table), "part right/wrong/none": parts(table)}
            for name, table in tables.items()
        ]
    )

    print("\nAlert and danger, all 8 features (danger: rms held at the threshold, too):\n")
    levels = {"alert": "warning_op_h", "danger": "danger_op_h"}
    print_table([{"level": level, **summary(detector, column)} for level, column in levels.items()])
    print(
        "\nmedian warning: operating hours from the level to the end, detected failures only."
        "\nper 1000 h: false alarms per 1000 operating hours of surviving bearings."
        "\npart: diagnosis at the end against the readme, or no part named."
    )


if __name__ == "__main__":
    main()
