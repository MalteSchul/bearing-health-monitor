"""Evaluate the detector on every run: warning time, diagnosis and false alarms per bearing.

    uv run python scripts/evaluate_detector.py

Reads the feature table the app serves. Thresholds were chosen on set 2 only; sets 1 and 3 are
evaluated unchanged, so their numbers are the honest ones. The same detector is also run on
feature subsets: rms alone is the usual overall-level alarm, the others show what each domain adds.
"""

from datetime import datetime

import pandas as pd
from scipy import stats

from monitor.config import Settings
from monitor.features import FEATURES
from monitor.health import BASELINE, MAX_GAP, History, assess
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


def operating_hours(times: pd.Series, start: datetime, end: datetime) -> float:
    """Hours the rig ran between two times: steps across a stop do not count."""
    window = times[(times >= start) & (times <= end)].sort_values()
    steps = window.diff()
    return round(float(steps[steps <= MAX_GAP].dt.total_seconds().sum()) / 3600, 1)


def restrict(run: pd.DataFrame, keep: list[str]) -> pd.DataFrame:
    """The run with every other feature pinned to a constant, whose ratio never leaves 1."""
    pinned = run.copy()
    pinned[[name for name in FEATURES if name not in keep]] = 1.0
    return pinned


def evaluate(
    history: History, start: datetime, times: pd.Series, failure: str | None
) -> dict[str, object]:
    end = history.conditions[-1]
    alarm = end.alarm_at
    at_alarm = None if alarm is None else history.at(alarm)
    crosstalk = next((c.as_of for c in history.conditions if c.status == "crosstalk"), None)
    if failure is None:
        verdict = "false alarm" if alarm else "ok"
    else:
        verdict = "detected" if alarm else "missed"
    return {
        "bearing": history.bearing,
        "failure": failure or "-",
        "status": end.status,
        "alarm_h": hours(start, alarm),
        "end_h": hours(start, end.as_of),
        "warning_h": hours(alarm, end.as_of) if alarm else None,
        # The rig stood still for days in set 1; damage only grows while it runs.
        "warning_op_h": operating_hours(times, alarm, end.as_of) if alarm else None,
        "diagnosis_at_alarm": at_alarm.diagnosis if at_alarm else None,
        "diagnosis_at_end": end.diagnosis,
        "first_crosstalk_h": hours(start, crosstalk),
        "verdict": verdict,
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


def rate(hits: int, total: int) -> str:
    # Exact (Clopper-Pearson) interval: with 4 failures and 8 survivors, the rates are anecdotes.
    low, high = stats.binomtest(hits, total).proportion_ci(confidence_level=0.95, method="exact")
    return f"{hits}/{total} (95% CI {low:.0%}-{high:.0%})"


def summary(table: pd.DataFrame) -> dict[str, object]:
    failed = table[table["failure"] != "-"]
    survivors = table[table["failure"] == "-"]
    detected = failed[failed["verdict"] == "detected"]
    false_alarms = int((survivors["verdict"] == "false alarm").sum())
    return {
        "detected": rate(len(detected), len(failed)),
        "median warning (op h)": round(float(detected["warning_op_h"].median()), 1),
        "false alarms": rate(false_alarms, len(survivors)),
        "per 1000 survivor op h": round(false_alarms / survivors["monitored_op_h"].sum() * 1000, 2),
        "right part at end": f"{(failed['diagnosis_at_end'] == failed['failure']).sum()}"
        f"/{len(failed)}",
    }


def main() -> None:
    features = pd.read_parquet(Settings().features_path)
    pd.set_option("display.width", 250)

    tables = {name: results(features, keep) for name, keep in VARIANTS.items()}
    detector = tables["all 8 (detector)"]
    print(detector.drop(columns="monitored_op_h").to_string(index=False, na_rep="-"))

    print("\nWarning in operating hours before the run ended, per failure and feature set:")
    warnings = pd.DataFrame(
        {
            name: table[table["failure"] != "-"].set_index(["run", "bearing", "failure"])[
                "warning_op_h"
            ]
            for name, table in tables.items()
        }
    )
    print(warnings.to_string(na_rep="missed"))

    print("\nSummary per feature set (same alarm rule, same thresholds):")
    overview = pd.DataFrame({name: summary(table) for name, table in tables.items()}).T
    print(overview.to_string())


if __name__ == "__main__":
    main()
