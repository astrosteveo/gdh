"""Probe findings on the testbed scenes must match tests/expected_findings.json.

The blind scenes are the set the probe rules were tuned on (see
testbed/blind/KEY.md), so this guards against regressions. It doesn't
measure accuracy on new scenes.
"""
import json
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
