from dataclasses import fields
from datetime import datetime, timedelta

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from monitor.api import create_app
from monitor.config import Settings
from monitor.features import FEATURES, ChannelFeatures
from monitor.store import ChannelSeries

# 30 hours of 10-minute snapshots: the first 24 are the baseline, the rest can raise an alarm.
SNAPSHOTS = 180
START = datetime(2004, 2, 12, 10, 0)
FAULT_FROM = START + timedelta(hours=26)
ALARM_AT = FAULT_FROM + timedelta(hours=1)


def feature_rows(experiment: str, bearing: int, channel: int) -> list[dict[str, object]]:
    return [
        {
            "experiment": experiment,
            "timestamp": START + timedelta(minutes=10 * i),
            "bearing": bearing,
            "channel": channel,
            # Distinct per feature, channel and snapshot, so a mix-up shows.
            **{name: channel + k / 10 + i / 1000 for k, name in enumerate(FEATURES)},
        }
        for i in range(SNAPSHOTS)
    ]


@pytest.fixture(autouse=True)
def synthetic_features(tmp_path, monkeypatch):
    """A small feature table in the shape scripts/extract_features.py writes."""
    rows = [
        *feature_rows("set1", bearing=3, channel=6),  # shuffled on purpose
        *feature_rows("set1", bearing=3, channel=5),
        *feature_rows("set2", bearing=1, channel=1),
        *feature_rows("set2", bearing=2, channel=2),
    ]
    frame = pd.DataFrame(rows).sample(frac=1, random_state=0)
    failing = (frame["experiment"] == "set2") & (frame["bearing"] == 1)
    frame.loc[failing & (frame["timestamp"] >= FAULT_FROM), "env_bpfo"] *= 10
    frame[list(FEATURES)] = frame[list(FEATURES)].astype("float32")
    path = tmp_path / "features.parquet"
    frame.to_parquet(path, index=False)
    monkeypatch.setenv("MONITOR_FEATURES_PATH", str(path))


def make_client(**overrides) -> TestClient:
    return TestClient(create_app(Settings(**overrides)))


def test_health_endpoint_is_outside_versioned_api(tmp_path):
    client = make_client(frontend_dir=tmp_path)

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_frontend_is_served_from_configured_directory(tmp_path):
    (tmp_path / "index.html").write_text("<p>custom frontend</p>")
    client = make_client(frontend_dir=tmp_path)

    response = client.get("/")

    assert response.status_code == 200
    assert "custom frontend" in response.text


def test_static_frontend_does_not_shadow_api(tmp_path):
    (tmp_path / "index.html").write_text("<p>frontend</p>")
    client = make_client(frontend_dir=tmp_path)

    assert client.get("/api/health").json() == {"status": "ok"}


def test_missing_frontend_directory_still_serves_api(tmp_path):
    client = make_client(frontend_dir=tmp_path / "does-not-exist")

    assert client.get("/api/health").status_code == 200
    assert client.get("/").status_code == 404


def test_cors_allows_configured_origin_only(tmp_path):
    client = make_client(frontend_dir=tmp_path, cors_origins=["http://localhost:5173"])

    allowed = client.get("/api/health", headers={"Origin": "http://localhost:5173"})
    other = client.get("/api/health", headers={"Origin": "http://evil.example"})

    assert allowed.headers.get("access-control-allow-origin") == "http://localhost:5173"
    assert "access-control-allow-origin" not in other.headers


def test_version_reports_dev_outside_a_build(tmp_path, monkeypatch):
    monkeypatch.delenv("MONITOR_COMMIT_SHA", raising=False)
    client = make_client(frontend_dir=tmp_path)

    assert client.get("/api/version").json() == {"commit": "dev"}


def test_version_reports_the_build_commit(tmp_path):
    client = make_client(frontend_dir=tmp_path, commit_sha="3ef6878")

    assert client.get("/api/version").json() == {"commit": "3ef6878"}
    assert client.get("/openapi.json").json()["info"]["version"] == "3ef6878"


def test_experiments_list_bearings_channels_and_documented_failures(tmp_path):
    client = make_client(frontend_dir=tmp_path)

    experiments = {e["name"]: e for e in client.get("/api/v1/experiments").json()}

    assert set(experiments) == {"set1", "set2"}
    set2 = experiments["set2"]
    assert set2["snapshots"] == SNAPSHOTS
    assert set2["first"] == START.isoformat()
    assert [(b["bearing"], b["channels"], b["failure"]) for b in set2["bearings"]] == [
        (1, [1], "outer race"),
        (2, [2], None),
    ]
    assert experiments["set1"]["bearings"][0]["channels"] == [5, 6]


def test_bearings_of_an_experiment(tmp_path):
    client = make_client(frontend_dir=tmp_path)

    [bearing] = client.get("/api/v1/experiments/set1/bearings").json()

    assert (bearing["bearing"], bearing["channels"], bearing["failure"]) == (
        3,
        [5, 6],
        "inner race",
    )


def test_bearings_report_their_latest_condition(tmp_path):
    client = make_client(frontend_dir=tmp_path)

    failing, healthy = client.get("/api/v1/experiments/set2/bearings").json()

    last = (START + timedelta(minutes=10 * (SNAPSHOTS - 1))).isoformat()
    assert failing["condition"] == {
        "as_of": last,
        "status": "alarm",
        "index": pytest.approx(10, rel=0.1),
        "driver": "env_bpfo",
        "diagnosis": "outer race",
        "alarm_at": ALARM_AT.isoformat(),
        "crosstalk_from": None,
    }
    assert (healthy["condition"]["status"], healthy["condition"]["alarm_at"]) == ("ok", None)


def test_health_index_series_is_aligned_with_its_timestamps(tmp_path):
    client = make_client(frontend_dir=tmp_path)

    body = client.get("/api/v1/experiments/set2/bearings/1/health-index").json()

    assert (body["experiment"], body["bearing"], body["threshold"]) == ("set2", 1, 2.0)
    series = list(
        zip(body["timestamps"], body["index"], body["driver"], body["status"], strict=True)
    )
    assert len(series) == SNAPSHOTS
    # No index while the baseline is recorded, then the alarm from the moment it was raised.
    assert series[0] == (START.isoformat(), None, None, "baseline")
    assert {status for _, _, _, status in series[:144]} == {"baseline"}
    assert [t for t, _, _, status in series if status == "alarm"][0] == ALARM_AT.isoformat()


def test_single_experiment_matches_its_entry_in_the_list(tmp_path):
    client = make_client(frontend_dir=tmp_path)

    listed = {e["name"]: e for e in client.get("/api/v1/experiments").json()}

    assert client.get("/api/v1/experiments/set2").json() == listed["set2"]


def test_single_bearing_matches_its_entry_in_the_list(tmp_path):
    client = make_client(frontend_dir=tmp_path)

    listed = client.get("/api/v1/experiments/set2/bearings").json()

    assert client.get("/api/v1/experiments/set2/bearings/2").json() == listed[1]


def test_every_prefix_of_a_bearing_url_is_a_resource(tmp_path):
    client = make_client(frontend_dir=tmp_path)
    path = "/api/v1/experiments/set2/bearings/1/features"
    parts = path.split("/")

    prefixes = ["/".join(parts[:n]) for n in range(4, len(parts) + 1)]

    assert prefixes[0] == "/api/v1/experiments"
    assert [client.get(p).status_code for p in prefixes] == [200] * len(prefixes)


def test_bearing_features_are_columnar_and_in_time_order(tmp_path):
    client = make_client(frontend_dir=tmp_path)

    body = client.get("/api/v1/experiments/set2/bearings/1/features").json()

    assert (body["experiment"], body["bearing"], body["failure"]) == ("set2", 1, "outer race")
    [channel] = body["channels"]
    assert channel["channel"] == 1
    assert channel["timestamps"] == sorted(channel["timestamps"])
    assert len(channel["timestamps"]) == SNAPSHOTS
    assert channel["rms"][:2] == [1.0, 1.001]
    assert channel["kurtosis"][-1] == pytest.approx(1.3 + (SNAPSHOTS - 1) / 1000)


def test_bearing_with_two_sensors_returns_both_channels(tmp_path):
    client = make_client(frontend_dir=tmp_path)

    body = client.get("/api/v1/experiments/set1/bearings/3/features").json()

    assert [c["channel"] for c in body["channels"]] == [5, 6]
    assert body["channels"][1]["rms"][0] == 6.0


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/experiments/set9",
        "/api/v1/experiments/set9/bearings",
        "/api/v1/experiments/set9/bearings/1",
        "/api/v1/experiments/set2/bearings/3",
        "/api/v1/experiments/set9/bearings/1/features",
        "/api/v1/experiments/set2/bearings/3/features",
        "/api/v1/experiments/set2/bearings/3/health-index",
    ],
)
def test_unknown_experiment_or_bearing_is_404(tmp_path, path):
    client = make_client(frontend_dir=tmp_path)

    assert client.get(path).status_code == 404


def test_feature_series_are_gzipped_when_the_client_accepts_it(tmp_path):
    client = make_client(frontend_dir=tmp_path)

    response = client.get(
        "/api/v1/experiments/set1/bearings/3/features", headers={"Accept-Encoding": "gzip"}
    )

    assert response.headers["content-encoding"] == "gzip"


def test_app_does_not_start_without_the_feature_table(tmp_path):
    with pytest.raises(FileNotFoundError):
        create_app(Settings(frontend_dir=tmp_path, features_path=tmp_path / "missing.parquet"))


def test_response_model_has_a_series_for_every_feature():
    assert set(ChannelSeries.model_fields) == {"channel", "timestamps"} | {
        f.name for f in fields(ChannelFeatures)
    }
