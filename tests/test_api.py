from dataclasses import fields
from datetime import datetime, timedelta

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from monitor.api import create_app
from monitor.config import Settings
from monitor.copilot import Unavailable
from monitor.features import FEATURES, ChannelFeatures
from monitor.store import ChannelSeries

# 30 hours of 10-minute snapshots: the first 24 are the baseline, the rest can raise an alert.
SNAPSHOTS = 180
START = datetime(2004, 2, 12, 10, 0)
FAULT_FROM = START + timedelta(hours=26)
ALERT_AT = FAULT_FROM + timedelta(hours=1)


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
    assert [(b["bearing"], b["channels"], b["documented_failure"]) for b in set2["bearings"]] == [
        (1, [1], "outer race"),
        (2, [2], None),
    ]
    assert experiments["set1"]["bearings"][0]["channels"] == [5, 6]


def test_bearings_of_an_experiment(tmp_path):
    client = make_client(frontend_dir=tmp_path)

    [bearing] = client.get("/api/v1/experiments/set1/bearings").json()

    assert (bearing["bearing"], bearing["channels"], bearing["documented_failure"]) == (
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
        "status": "alert",
        "index": pytest.approx(10, rel=0.1),
        "driver": "env_bpfo",
        "part_levels": pytest.approx(
            {"outer race": 10, "inner race": 1, "roller element": 1, "cage": 1}, rel=0.1
        ),
        "diagnosis": "outer race",
        "alert_at": ALERT_AT.isoformat(),
        "danger_at": None,
        "crosstalk_from": None,
        "crosstalk_feature": None,
        "crosstalk_factor": None,
    }
    assert (healthy["condition"]["status"], healthy["condition"]["alert_at"]) == ("ok", None)


def test_health_index_series_is_aligned_with_its_timestamps(tmp_path):
    client = make_client(frontend_dir=tmp_path)

    body = client.get("/api/v1/experiments/set2/bearings/1/health-index").json()

    assert (body["experiment"], body["bearing"], body["threshold"]) == ("set2", 1, 2.0)
    series = list(
        zip(body["timestamps"], body["index"], body["driver"], body["status"], strict=True)
    )
    assert len(series) == SNAPSHOTS
    # No index while the baseline is recorded, then the alert from the moment it was raised.
    assert series[0] == (START.isoformat(), None, None, "baseline")
    assert {status for _, _, _, status in series[:144]} == {"baseline"}
    assert [t for t, _, _, status in series if status == "alert"][0] == ALERT_AT.isoformat()


def test_health_index_is_the_largest_ratio_at_each_snapshot(tmp_path):
    client = make_client(frontend_dir=tmp_path)

    ratios = client.get("/api/v1/experiments/set2/bearings/1/ratios").json()
    health = client.get("/api/v1/experiments/set2/bearings/1/health-index").json()

    assert ratios["timestamps"] == health["timestamps"]
    by_feature = [ratios[name] for name in FEATURES]
    largest = [None if v[0] is None else max(v) for v in zip(*by_feature, strict=True)]
    assert largest == health["index"]
    assert ratios["env_bpfo"][-1] == pytest.approx(10, rel=0.1)


def test_ratios_take_the_worse_of_two_channels(tmp_path):
    client = make_client(frontend_dir=tmp_path)

    body = client.get("/api/v1/experiments/set1/bearings/3/ratios").json()

    # Both channels grow by the same step; from the smaller baseline that is the larger ratio.
    last, baseline_median = SNAPSHOTS - 1, 71.5 / 1000  # rms offset of snapshots 0-143
    channel5, channel6 = ((c + last / 1000) / (c + baseline_median) for c in (5, 6))
    assert channel5 > channel6
    assert body["rms"][last] == round(channel5, 3)


def condition_at(client: TestClient, path: str, at: datetime) -> dict[str, object]:
    response = client.get(path, params={"at": at.isoformat()})
    assert response.status_code == 200, response.text
    condition: dict[str, object] = response.json()["condition"]
    return condition


def test_condition_as_of_a_time_shows_what_was_known_then(tmp_path):
    client = make_client(frontend_dir=tmp_path)
    path = "/api/v1/experiments/set2/bearings/1"

    learning = condition_at(client, path, START + timedelta(hours=12))
    rising = condition_at(client, path, FAULT_FROM + timedelta(minutes=30))
    raised = condition_at(client, path, ALERT_AT + timedelta(minutes=5))

    assert (learning["status"], learning["index"]) == ("baseline", None)
    assert (rising["status"], rising["alert_at"]) == ("ok", None)
    # Between snapshots the answer comes from the latest one before.
    assert (raised["status"], raised["as_of"]) == ("alert", ALERT_AT.isoformat())


def test_experiment_reports_its_machine_as_of_a_time(tmp_path):
    client = make_client(frontend_dir=tmp_path)

    def machine(at=None):
        params = {} if at is None else {"at": at.isoformat()}
        return client.get("/api/v1/experiments/set2", params=params).json()["machine"]

    assert machine(START + timedelta(hours=12)) == {"status": "baseline", "bearings": [1, 2]}
    assert machine(FAULT_FROM + timedelta(minutes=30)) == {"status": "ok", "bearings": []}
    assert machine() == {"status": "alert", "bearings": [1]}


def test_at_applies_to_the_experiment_and_its_bearings_list(tmp_path):
    client = make_client(frontend_dir=tmp_path)
    at = {"at": (FAULT_FROM + timedelta(minutes=30)).isoformat()}

    experiment = client.get("/api/v1/experiments/set2", params=at).json()
    listed = client.get("/api/v1/experiments/set2/bearings", params=at).json()
    single = client.get("/api/v1/experiments/set2/bearings/1", params=at).json()

    assert experiment["bearings"] == listed
    assert listed[0] == single
    assert single["condition"]["status"] == "ok"


def test_condition_before_the_first_snapshot_is_404(tmp_path):
    client = make_client(frontend_dir=tmp_path)

    response = client.get(
        "/api/v1/experiments/set2/bearings/1", params={"at": "2004-01-01T00:00:00"}
    )

    assert response.status_code == 404
    assert START.isoformat() in response.json()["detail"]


def test_at_with_a_utc_offset_is_rejected(tmp_path):
    client = make_client(frontend_dir=tmp_path)

    response = client.get(
        "/api/v1/experiments/set2/bearings/1", params={"at": "2004-02-13T12:00:00Z"}
    )

    assert response.status_code == 422


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

    assert (body["experiment"], body["bearing"], body["documented_failure"]) == (
        "set2",
        1,
        "outer race",
    )
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
        "/api/v1/experiments/set2/bearings/3/ratios",
        "/api/v1/experiments/set9/evaluation",
    ],
)
def test_unknown_experiment_or_bearing_is_404(tmp_path, path):
    client = make_client(frontend_dir=tmp_path)

    assert client.get(path).status_code == 404


def test_evaluation_compares_each_alert_with_the_documented_end(tmp_path):
    client = make_client(frontend_dir=tmp_path)

    body = client.get("/api/v1/experiments/set2/evaluation").json()

    # No stops in the synthetic run, so operating hours are wall-clock hours.
    last = START + timedelta(minutes=10 * (SNAPSHOTS - 1))
    lead = round((last - ALERT_AT).total_seconds() / 3600, 1)
    assert body == {
        "experiment": "set2",
        "bearings": [
            {
                "bearing": 1,
                "documented_failure": "outer race",
                "verdict": "detected",
                "alert_lead_op_h": lead,
                "danger_lead_op_h": None,
            },
            {
                "bearing": 2,
                "documented_failure": None,
                "verdict": "quiet",
                "alert_lead_op_h": None,
                "danger_lead_op_h": None,
            },
        ],
        "failures": 1,
        "failures_alerted": 1,
        "survivors": 1,
        "false_alarms": 0,
    }


def test_evaluation_counts_a_documented_failure_without_an_alert_as_missed(tmp_path):
    client = make_client(frontend_dir=tmp_path)

    body = client.get("/api/v1/experiments/set1/evaluation").json()

    assert [(b["bearing"], b["verdict"]) for b in body["bearings"]] == [(3, "missed")]
    assert (body["failures"], body["failures_alerted"]) == (1, 0)


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


COPILOT = "/api/v1/experiments/set2/copilot"


class FakeWriter:
    """Stands in for the model: records what it was sent and answers with a fixed text."""

    def __init__(self, answer: str = "Bearing 1 is in alert [1].") -> None:
        self.answer = answer
        self.prompts: list[str] = []

    def __call__(self, system: str, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.answer


def copilot_client(tmp_path, writer: object = None, **overrides: object) -> TestClient:
    settings = Settings(frontend_dir=tmp_path, anthropic_api_key=None, **overrides)
    return TestClient(create_app(settings, writer=writer))  # type: ignore[arg-type]


def test_copilot_without_a_key_returns_the_facts_but_no_answer(tmp_path):
    client = copilot_client(tmp_path)

    body = client.post(COPILOT, json={"question": "Why is bearing 1 yellow?", "bearing": 1}).json()

    assert body["answer"] is None
    assert "No API key" in body["note"]
    rig, first = body["sources"][:2]
    assert (rig["id"], rig["kind"], rig["text"]) == (1, "detector", "Rig: alert, from bearing 1.")
    assert first["text"].startswith(f"Bearing 1: alert since {ALERT_AT:%Y-%m-%d %H:%M}")
    texts = [s["text"] for s in body["sources"]]
    assert "For bearing 1 (alert): Alert (yellow)" in " ".join(texts), "explained, and for whom"
    assert any("2,000 rpm" in t for t in texts), "machine facts always included"


def test_copilot_facts_use_plain_names_never_code_names(tmp_path):
    client = copilot_client(tmp_path)

    body = client.post(COPILOT, json={"question": "Why?", "bearing": 1}).json()

    texts = " ".join(s["text"] for s in body["sources"])
    assert "highest feature: outer race signal" in texts
    assert [code for code in FEATURES if "_" in code and code in texts] == []


def test_copilot_sends_the_facts_and_the_tagged_question_to_the_writer(tmp_path):
    writer = FakeWriter()
    client = copilot_client(tmp_path, writer=writer)

    body = client.post(COPILOT, json={"question": "What should I do?", "bearing": 1}).json()

    assert body["answer"] == "Bearing 1 is in alert [1]."
    [sent] = writer.prompts
    assert "The technician is looking at bearing 1." in sent
    assert f"[1] {body['sources'][0]['text']}" in sent
    assert sent.endswith("<question>What should I do?</question>")


def test_copilot_answers_about_the_whole_run_without_a_bearing(tmp_path):
    client = copilot_client(tmp_path)

    body = client.post(COPILOT, json={"question": "Which bearing is the problem?"}).json()

    detector = [s["text"] for s in body["sources"] if s["kind"] == "detector"]
    # The rig, then each bearing's status and its trend.
    assert [t.split(":")[0] for t in detector[:4]] == [
        "Rig",
        "Bearing 1",
        "Bearing 1 health index",
        "Bearing 2",
    ]


def test_copilot_lists_bearings_in_rig_order_whichever_is_in_focus(tmp_path):
    client = copilot_client(tmp_path)

    body = client.post(COPILOT, json={"question": "Status?", "bearing": 2}).json()

    assert body["sources"][1]["text"].startswith("Bearing 1:")


def test_copilot_only_knows_what_was_known_at_that_time(tmp_path):
    client = copilot_client(tmp_path)
    at = {"at": (FAULT_FROM + timedelta(minutes=30)).isoformat()}

    body = client.post(COPILOT, params=at, json={"question": "Status?", "bearing": 1}).json()

    assert body["sources"][1]["text"].startswith("Bearing 1: ok;")
    assert body["sources"][1]["ref"] == f"Detector, as of {FAULT_FROM:%Y-%m-%d} 12:30"


def test_copilot_reports_why_the_writer_gave_no_answer(tmp_path):
    reason = "The copilot's language model could not be reached."

    def unavailable(system: str, prompt: str) -> str:
        raise Unavailable(reason)

    client = copilot_client(tmp_path, writer=unavailable)

    body = client.post(COPILOT, json={"question": "Why?"}).json()

    assert (body["answer"], body["note"]) == (None, reason)
    assert body["sources"]


@pytest.mark.parametrize(
    ("path", "payload", "status"),
    [
        ("/api/v1/experiments/set9/copilot", {"question": "Why?"}, 404),
        (COPILOT, {"question": "Why?", "bearing": 7}, 422),
        (COPILOT, {"question": "   "}, 422),
        (COPILOT, {"question": "x" * 501}, 422),
    ],
)
def test_copilot_rejects_unknown_runs_bearings_and_bad_questions(tmp_path, path, payload, status):
    client = copilot_client(tmp_path)

    assert client.post(path, json=payload).status_code == status


def test_copilot_before_the_first_snapshot_is_404(tmp_path):
    client = copilot_client(tmp_path)
    at = {"at": (START - timedelta(hours=1)).isoformat()}

    assert client.post(COPILOT, params=at, json={"question": "Why?"}).status_code == 404


def test_copilot_limits_questions_per_minute(tmp_path):
    client = copilot_client(tmp_path, writer=FakeWriter(), copilot_questions_per_minute=2)

    codes = [client.post(COPILOT, json={"question": "Why?"}).status_code for _ in range(3)]

    assert codes == [200, 200, 429]
