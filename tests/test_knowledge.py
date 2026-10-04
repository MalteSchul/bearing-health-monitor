import re
import tomllib
from importlib.resources import files
from typing import get_args

import pytest

from monitor.features import FEATURES
from monitor.health import PARTS, Status
from monitor.ims import FAULT_FREQUENCIES
from monitor.knowledge import Knowledge

KNOWLEDGE = Knowledge.load()
# Python identifiers, such as env_bpfo: words for the code, not for people.
CODE_NAMES = [feature for feature in FEATURES if "_" in feature]


def write(tmp_path, text: str):
    path = tmp_path / "knowledge.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_every_word_the_detector_reports_is_a_node():
    reported = [*get_args(Status), *PARTS.values(), *FEATURES]

    assert [word for word in reported if word not in KNOWLEDGE] == []


def test_every_feature_has_a_plain_name():
    assert [KNOWLEDGE.name(code) for code in CODE_NAMES if "_" in KNOWLEDGE.name(code)] == []
    assert KNOWLEDGE.name("env_bpfo") == "outer race signal"
    assert KNOWLEDGE.name("rms") == "rms", "a node without a name is called by its id"


def test_no_text_uses_a_code_name():
    nodes = tomllib.loads(files("monitor").joinpath("knowledge.toml").read_text("utf-8"))["node"]

    assert [n["id"] for n in nodes if any(code in n["text"] for code in CODE_NAMES)] == []


def test_machine_facts_cite_the_fault_frequencies_the_detector_uses():
    [geometry] = [f for f in KNOWLEDGE.machine() if f.id == "geometry"]
    stated = {float(hz) for hz in re.findall(r"(\d+\.\d) Hz", geometry.text)}

    computed = {round(hz, 1) for hz in vars(FAULT_FREQUENCIES).values()}

    assert stated == computed


def test_machine_facts_carry_their_full_source():
    facts = KNOWLEDGE.machine()

    assert {f.id for f in facts} >= {"rig", "load", "sampling", "design life"}
    assert all(f.label == "Machine" for f in facts)
    assert all("readme" in f.source or "Qiu" in f.source or "detector" in f.source for f in facts)


def test_part_leads_to_its_damage_types_and_their_causes():
    ids = [f.id for f, _ in KNOWLEDGE.around(["outer race"])]

    assert ids[0] == "outer race"
    assert {"subsurface fatigue", "surface fatigue", "indentation"} <= set(ids)
    assert {"end of life", "poor lubrication", "contamination"} <= set(ids)


def test_lookup_stops_after_two_hops():
    # cage -> cage damage -> roller element; the roller's damage types would be a third hop.
    ids = {f.id for f, _ in KNOWLEDGE.around(["cage"])}

    assert {"cage damage", "roller element"} <= ids
    assert "subsurface fatigue" not in ids


@pytest.mark.parametrize("feature", FEATURES)
def test_a_feature_never_leads_to_a_part(feature):
    # Naming the part is the detector's diagnosis; a feature pointing to one would guess it again.
    assert "Component" not in {f.label for f, _ in KNOWLEDGE.around([feature])}


@pytest.mark.parametrize("status", ["ok", "crosstalk", "alert", "danger"])
def test_every_status_with_an_index_reaches_its_definition(status):
    ids = {f.id for f, _ in KNOWLEDGE.around([status])}

    assert {"health index rule", "baseline rule"} <= ids


def test_rules_bring_the_rules_they_build_on_however_deep():
    # danger -> danger rule -> alert rule -> health index rule -> baseline rule: four arrows.
    reached = {f.id: origins for f, origins in KNOWLEDGE.around(["danger"])}

    assert reached["baseline rule"] == ["danger"]


def test_arrows_are_only_followed_forwards():
    # Causes explain damage; they lead nowhere, so a cause alone reaches only itself.
    assert [f.id for f, _ in KNOWLEDGE.around(["contamination"])] == ["contamination"]


def test_status_leads_to_its_rules_and_actions():
    ids = [f.id for f, _ in KNOWLEDGE.around(["danger"])]

    assert {"danger rule", "act now", "check for secondary damage"} <= set(ids)
    assert "plan the replacement" not in ids


def test_overlapping_lookups_list_each_fact_once_nearest_first():
    ids = [f.id for f, _ in KNOWLEDGE.around(["alert", "danger"])]

    assert len(ids) == len(set(ids))
    assert ids[0] == "alert"


def test_each_fact_says_which_starts_reached_it():
    reached = {f.id: origins for f, origins in KNOWLEDGE.around(["alert", "danger"])}

    assert reached["act now"] == ["danger"]
    assert reached["inspect every bearing"] == ["alert", "danger"]


def test_unknown_source_stops_loading(tmp_path):
    path = write(
        tmp_path,
        '[sources]\na = "A"\n[[node]]\nid = "x"\nlabel = "Rule"\nsource = "b"\ntext = "t"\n',
    )

    with pytest.raises(ValueError, match="unknown source"):
        Knowledge.load(path)


def test_arrow_to_a_missing_node_stops_loading(tmp_path):
    path = write(
        tmp_path,
        '[sources]\na = "A"\n[[node]]\nid = "x"\nlabel = "Rule"\nsource = "a"\ntext = "t"\n'
        'out = { CAUSED_BY = ["y"] }\n',
    )

    with pytest.raises(ValueError, match="unknown node"):
        Knowledge.load(path)
