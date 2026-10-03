from datetime import datetime

import numpy as np
import pytest

from monitor.ims import (
    FAULT_FREQUENCIES,
    SAMPLES_PER_SNAPSHOT,
    Experiment,
    SnapshotSummary,
    fault_frequencies,
    summarize_snapshot,
    validate_experiment,
)

EXPERIMENT = Experiment(
    name="tiny",
    bearings=(1, 2),
    n_snapshots=3,
    first=datetime(2004, 2, 12, 10, 0, 0),
    last=datetime(2004, 2, 12, 10, 20, 0),
    silent_snapshots=frozenset({"2004.02.12.10.20.00"}),
)
SHAPE = (SAMPLES_PER_SNAPSHOT, 2)


def healthy(name: str) -> SnapshotSummary:
    return SnapshotSummary(name=name, shape=SHAPE, rms=(0.1, 0.08))


def silent(name: str) -> SnapshotSummary:
    return SnapshotSummary(name=name, shape=SHAPE, rms=(0.002, 0.003))


def expected_summaries() -> list[SnapshotSummary]:
    return [
        healthy("2004.02.12.10.00.00"),
        healthy("2004.02.12.10.10.00"),
        silent("2004.02.12.10.20.00"),
    ]


def test_matching_experiment_has_no_problems():
    assert validate_experiment(EXPERIMENT, expected_summaries()) == []


def test_missing_snapshot_is_reported():
    summaries = expected_summaries()[1:]

    problems = validate_experiment(EXPERIMENT, summaries)

    assert "2 snapshots, expected 3" in problems
    assert any(p.startswith("snapshots span") for p in problems)


def test_new_silent_snapshot_is_reported():
    summaries = expected_summaries()
    summaries[1] = silent(summaries[1].name)

    problems = validate_experiment(EXPERIMENT, summaries)

    assert any(p.startswith("silent snapshots") for p in problems)


def test_wrong_shape_and_unreadable_snapshots_are_reported():
    summaries = expected_summaries()
    summaries[0] = SnapshotSummary(name=summaries[0].name, shape=(100, 2), rms=(0.1, 0.1))
    summaries[1] = SnapshotSummary(name=summaries[1].name, error="unreadable: bad line")

    problems = validate_experiment(EXPERIMENT, summaries)

    assert f"2004.02.12.10.00.00: shape (100, 2), expected {SHAPE}" in problems
    assert "2004.02.12.10.10.00: unreadable: bad line" in problems


def test_non_timestamp_file_name_is_reported():
    summaries = [*expected_summaries(), healthy("notes.txt")]

    problems = validate_experiment(EXPERIMENT, summaries)

    assert any("not a timestamp" in p for p in problems)


def test_summarize_reads_tab_separated_crlf_snapshot(tmp_path):
    path = tmp_path / "2004.02.12.10.00.00"
    path.write_bytes(b"0.3\t-0.4\r\n-0.3\t0.4\r\n")

    summary = summarize_snapshot(path)

    assert summary.shape == (2, 2)
    assert np.allclose(summary.rms, (0.3, 0.4))
    assert summary.finite
    assert summary.error is None


def test_summarize_flags_non_finite_and_unparseable_snapshots(tmp_path):
    nan_file = tmp_path / "nan"
    nan_file.write_text("0.1\tnan\n0.2\t0.3\n")
    text_file = tmp_path / "text"
    text_file.write_text("0.1\tabc\n")

    assert not summarize_snapshot(nan_file).finite
    assert summarize_snapshot(text_file).error is not None


def test_fault_frequencies_match_published_values_for_the_rig():
    f = FAULT_FREQUENCIES

    assert f.ftf == pytest.approx(14.8, abs=0.1)
    assert f.bpfo == pytest.approx(236.4, abs=0.1)
    assert f.bpfi == pytest.approx(296.9, abs=0.1)
    assert f.bsf == pytest.approx(139.9, abs=0.1)


def test_outer_and_inner_race_rates_add_up_to_rollers_times_shaft_speed():
    f = fault_frequencies(
        shaft_hz=10, rollers=8, roller_diameter=1, pitch_diameter=5, contact_angle_deg=0
    )

    # Relative to the cage, outer and inner race together turn once per shaft revolution.
    assert f.bpfo + f.bpfi == pytest.approx(8 * 10)
    assert f.bpfo == pytest.approx(8 * f.ftf)
