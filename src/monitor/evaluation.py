"""How each bearing's alerts compare with the state the dataset documents at the end of its run.

Hindsight by definition: the documented failure is only known once a run is over, so nothing here
depends on a moment, and the detector never sees it.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import pandas as pd

from monitor.health import MAX_GAP, History

Verdict = Literal["detected", "missed", "false alert", "quiet"]


def operating_hours(times: pd.Series, start: datetime, end: datetime) -> float:
    """Hours the rig ran between two times: steps across a stop do not count."""
    window = times[(times >= start) & (times <= end)].sort_values()
    steps = window.diff()
    return round(float(steps[steps <= MAX_GAP].dt.total_seconds().sum()) / 3600, 1)


def verdict(alerted: bool, failure: str | None) -> Verdict:
    if failure is None:
        return "false alert" if alerted else "quiet"
    return "detected" if alerted else "missed"


@dataclass(frozen=True)
class BearingResult:
    bearing: int
    documented_failure: str | None
    verdict: Verdict
    # Operating hours from each level to the end of the run: the warning it gave. Damage only
    # grows while the rig runs, so days of standstill in set 1 do not count.
    alert_lead_op_h: float | None
    danger_lead_op_h: float | None


@dataclass(frozen=True)
class RunEvaluation:
    experiment: str
    bearings: list[BearingResult]
    failures: int
    failures_alerted: int
    survivors: int
    false_alerts: int


def judge(history: History, times: pd.Series, failure: str | None) -> BearingResult:
    """One bearing's verdict, from its condition at the end of the run."""
    end = history.conditions[-1]

    def lead(since: datetime | None) -> float | None:
        return None if since is None else operating_hours(times, since, end.as_of)

    return BearingResult(
        bearing=history.bearing,
        documented_failure=failure,
        verdict=verdict(end.alert_at is not None, failure),
        alert_lead_op_h=lead(end.alert_at),
        danger_lead_op_h=lead(end.danger_at),
    )


def evaluate_run(
    experiment: str, histories: list[History], times: pd.Series, failures: Mapping[int, str]
) -> RunEvaluation:
    results = [judge(h, times, failures.get(h.bearing)) for h in histories]
    failed = [r for r in results if r.documented_failure is not None]
    survived = [r for r in results if r.documented_failure is None]
    return RunEvaluation(
        experiment=experiment,
        bearings=results,
        failures=len(failed),
        failures_alerted=sum(r.verdict == "detected" for r in failed),
        survivors=len(survived),
        false_alerts=sum(r.verdict == "false alert" for r in survived),
    )
