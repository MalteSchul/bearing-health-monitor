import pandas as pd
import pytest

from monitor.evaluation import evaluate_run, judge, operating_hours, verdict
from monitor.health import Condition, History

START = pd.Timestamp("2004-02-12 10:00")


def hour(h: float) -> pd.Timestamp:
    return START + pd.Timedelta(hours=h)


def snapshots(hours: float, start: float = 0) -> pd.Series:
    """Every 10 minutes from `start` for `hours`, both ends included."""
    return pd.Series(pd.date_range(hour(start), hour(start + hours), freq="10min"))


def ended(
    bearing: int, end: float, alert: float | None = None, danger: float | None = None
) -> History:
    """A bearing's history reduced to its last condition, which is all a verdict reads."""
    status = "danger" if danger is not None else "alert" if alert is not None else "ok"
    last = Condition(
        as_of=hour(end).to_pydatetime(),
        status=status,
        index=1.0,
        driver="rms",
        alert_at=None if alert is None else hour(alert).to_pydatetime(),
        danger_at=None if danger is None else hour(danger).to_pydatetime(),
    )
    return History(bearing=bearing, conditions=(last,))


def test_operating_hours_leave_out_stops():
    # Two hours of recording, a six-day stop, then one more hour.
    times = pd.concat([snapshots(2), snapshots(1, start=6 * 24)])

    assert operating_hours(times, hour(0), hour(6 * 24 + 1)) == 3.0
    assert operating_hours(times, hour(1), hour(2)) == 1.0


@pytest.mark.parametrize(
    ("alerted", "failure", "expected"),
    [
        (True, "outer race", "detected"),
        (False, "outer race", "missed"),
        (True, None, "false alert"),
        (False, None, "quiet"),
    ],
)
def test_verdict_compares_the_alert_with_the_documented_end(alerted, failure, expected):
    assert verdict(alerted, failure) == expected


def test_leads_run_from_each_level_to_the_end_of_the_run():
    times = snapshots(30)

    result = judge(ended(1, end=30, alert=20, danger=26.5), times, "outer race")

    assert result.verdict == "detected"
    assert (result.alert_lead_op_h, result.danger_lead_op_h) == (10, 3.5)


def test_a_bearing_without_an_alert_has_no_lead():
    result = judge(ended(1, end=30), snapshots(30), None)

    assert result.verdict == "quiet"
    assert (result.alert_lead_op_h, result.danger_lead_op_h) == (None, None)


def test_run_totals_count_failures_and_survivors_separately():
    histories = [
        ended(1, end=30, alert=20),
        ended(2, end=30),
        ended(3, end=30, alert=25),
        ended(4, end=30),
    ]

    run = evaluate_run("set1", histories, snapshots(30), {1: "outer race", 2: "inner race"})

    assert [r.verdict for r in run.bearings] == ["detected", "missed", "false alert", "quiet"]
    assert (run.failures, run.failures_alerted, run.survivors, run.false_alerts) == (2, 1, 2, 1)
