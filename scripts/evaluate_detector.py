"""Evaluate the detector on every run: warning time, diagnosis and false alarms per bearing.

    uv run python scripts/evaluate_detector.py

Reads the feature table the app serves. Thresholds were chosen on set 2 only; sets 1 and 3 are
evaluated unchanged, so their numbers are the honest ones.
"""

from datetime import datetime

import pandas as pd

from monitor.config import Settings
from monitor.health import History, assess
from monitor.ims import EXPERIMENTS

TUNED_ON = {"set2"}


def hours(since: datetime, time: datetime | None) -> float | None:
    return None if time is None else round((time - since).total_seconds() / 3600, 1)


def evaluate(history: History, start: datetime, failure: str | None) -> dict[str, object]:
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
        "diagnosis_at_alarm": at_alarm.diagnosis if at_alarm else None,
        "diagnosis_at_end": end.diagnosis,
        "first_crosstalk_h": hours(start, crosstalk),
        "verdict": verdict,
    }


def main() -> None:
    features = pd.read_parquet(Settings().features_path)
    rows = []
    for experiment in EXPERIMENTS:
        run = features[features["experiment"] == experiment.name]
        start = run["timestamp"].min().to_pydatetime()
        for history in assess(run):
            row = evaluate(history, start, experiment.failures.get(history.bearing))
            role = "tune" if experiment.name in TUNED_ON else "test"
            rows.append({"run": experiment.name, "role": role, **row})
    table = pd.DataFrame(rows)
    print(table.to_string(index=False, na_rep="-"))

    failed = table[table["failure"] != "-"]
    survivors = table[table["failure"] == "-"]
    detected = failed[failed["verdict"] == "detected"]
    named = (failed["diagnosis_at_end"] == failed["failure"]).sum()
    print(f"\nFailures detected: {len(detected)} of {len(failed)}", end="")
    if len(detected):
        warning = detected["warning_h"]
        print(f", warning {warning.min():.0f} to {warning.max():.0f} h before the run ended")
    print(f"Diagnosis matches the documented failure at the end: {named} of {len(failed)}")
    false_alarms = (survivors["verdict"] == "false alarm").sum()
    print(f"False alarms on bearings that survived: {false_alarms} of {len(survivors)}")


if __name__ == "__main__":
    main()
