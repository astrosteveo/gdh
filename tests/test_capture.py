"""Probe findings on the testbed scenes must match tests/expected_findings.json.

The blind scenes are the set the probe rules were tuned on (see
testbed/blind/KEY.md), so this guards against regressions. It doesn't
measure accuracy on new scenes.
"""
import json
import os
from pathlib import Path

from conftest import TESTBED, gdh

EXPECTED = json.loads((Path(__file__).parent / "expected_findings.json").read_text())


def scene_path(name):
    return "res://" + name.replace("__", "/") + ".tscn"


def test_findings_match(tmp_path):
    scenes = [arg for name in EXPECTED for arg in ("--scene", scene_path(name))]
    gdh("capture", "--project", TESTBED, "--modes", "normal", "--out", tmp_path, *scenes)
    actual = {}
    for report in sorted(tmp_path.glob("*/report.json")):
        findings = json.loads(report.read_text())["findings"]
        actual[report.parent.name] = sorted([f["severity"], f["probe"], f["node"]] for f in findings)
    assert actual == EXPECTED


def test_pseudo_locale_finds_the_text_that_wont_fit(tmp_path):
    def findings(*extra):
        out = tmp_path / ("pseudo" if extra else "plain")
        gdh("capture", "--project", TESTBED, "--modes", "normal", "--out", out, "--scene", "res://locale/menu.tscn",
            *extra)
        return {f["node"]: f for f in json.loads((out / "report.json").read_text())["findings"]}

    assert findings() == {}  # in English, everything fits
    found = findings("--locale", "pseudo")
    assert sorted(found) == ["Panel/Narrow", "Panel/Status", "Panel/Tip"]
    assert all(f["probe"] == "text_overflow" for f in found.values())
    assert "it's cut off (clip text)" in found["Panel/Narrow"]["message"]
    assert found["Panel/Narrow"]["data"]["text"].startswith("[")  # what it drew: Godot's pseudolocalization
    assert "past the edge of Panel" in found["Panel/Status"]["message"]
    assert "Shows 1 of the 2 lines" in found["Panel/Tip"]["message"]


def test_live_start_takes_a_locale(tmp_path):
    session = f"test-locale-{os.getpid()}"
    for locale, check in (("fr", lambda v: v == "fr"), ("pseudo", lambda v: v is True)):
        gdh("live", "start", "--project", TESTBED, "--scene", "res://locale/menu.tscn", "--session", session,
            "--out", tmp_path / locale, "--locale", locale)
        try:
            expr = "TranslationServer.get_locale()" if locale == "fr" else "TranslationServer.pseudolocalization_enabled"
            value = json.loads(gdh("live", "eval", expr, "--session", session, "--json").stdout)["result"]["value"]
            assert check(value)
            if locale == "pseudo":
                outline = gdh("live", "snapshot", "Panel/Buttons", "--session", session).stdout
                assert "(Button) \"[" in outline  # snapshot reads the text as the button draws it
        finally:
            gdh("live", "stop", "--session", session)
