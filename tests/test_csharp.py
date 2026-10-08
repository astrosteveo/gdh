"""gdh with a C# project (testbed_cs): it builds the assemblies with dotnet
first, runs Godot's .NET build (godot-mono) without being told, and reports
a failed build in a sentence."""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import ROOT

if not (shutil.which("godot-mono") and shutil.which("dotnet")):
    pytest.skip("needs godot-mono and the .NET SDK on PATH", allow_module_level=True)

SESSION = f"test-cs-{os.getpid()}"


def godot_version():
    out = subprocess.run(["godot-mono", "--version"], capture_output=True, text=True).stdout
    return re.match(r"(\d+\.\d+(?:\.\d+)?)", out.strip().splitlines()[-1]).group(1)


def dotnet_framework():
    out = subprocess.run(["dotnet", "--list-runtimes"], capture_output=True, text=True).stdout
    majors = [int(m) for m in re.findall(r"Microsoft\.NETCore\.App (\d+)\.", out)]
    return f"net{max(majors)}.0"


def run(*args, check=True):
    """gdh with no GODOT set, so it has to pick the binary itself."""
    env = {k: v for k, v in os.environ.items() if k != "GODOT"}
    proc = subprocess.run([sys.executable, "-m", "gdh", *map(str, args)], capture_output=True, text=True,
                          timeout=600, env=env)
    if check and proc.returncode != 0:
        raise AssertionError(f"gdh {' '.join(map(str, args))} failed:\n{proc.stdout}\n{proc.stderr}")
    return proc


@pytest.fixture(scope="module")
def project(tmp_path_factory):
    directory = tmp_path_factory.mktemp("cs") / "testbed_cs"
    shutil.copytree(ROOT / "testbed_cs", directory, ignore=shutil.ignore_patterns(".godot"))
    (directory / "GdhCsTestbed.csproj").write_text(f"""<Project Sdk="Godot.NET.Sdk/{godot_version()}">
  <PropertyGroup>
    <TargetFramework>{dotnet_framework()}</TargetFramework>
    <EnableDynamicLoading>true</EnableDynamicLoading>
  </PropertyGroup>
</Project>
""")
    run("import", "--project", directory)
    return directory


def test_live_builds_and_runs_csharp(project, tmp_path):
    run("live", "start", "--project", project, "--session", SESSION, "--out", tmp_path)
    try:
        assert run("live", "eval", "scene.Answer()", "--session", SESSION).stdout.strip() == "42"
        run("live", "step", "10", "--session", SESSION)
        assert run("live", "eval", "scene.Ticks()", "--session", SESSION).stdout.strip() == "10"
    finally:
        run("live", "stop", "--session", SESSION, check=False)


def test_capture_runs_csharp(project, tmp_path):
    proc = run("capture", "--project", project, "--scene", "res://main.tscn", "--out", tmp_path, "--modes", "normal")
    assert "exit=0" in proc.stdout and "errors=0" in proc.stdout
    assert (tmp_path / "normal.png").exists()


def test_failed_build_is_reported(project, tmp_path):
    broken = project / "Broken.cs"
    broken.write_text("public partial class Broken : Godot.Node { int x = ; }\n")
    try:
        proc = run("live", "start", "--project", project, "--session", SESSION, "--out", tmp_path, check=False)
        assert proc.returncode != 0
        assert "dotnet build GdhCsTestbed.csproj failed" in proc.stderr
        assert "Broken.cs" in proc.stderr
    finally:
        broken.unlink()
        run("live", "stop", "--session", SESSION, check=False)


def test_editor_runs_csharp_tool_scripts(project, tmp_path, monkeypatch):
    """gdh editor builds the project, and the editor runs its C# tool scripts: what one makes in the editor shows."""
    monkeypatch.setenv("GDH_EDITOR_HOME", str(tmp_path / "editor-home"))
    tool = project / "Grower.cs"
    tool.write_text("using Godot;\n\n[Tool]\npublic partial class Grower : Node3D\n{\n"
                    "    public override void _Ready()\n    {\n"
                    "        if (Engine.IsEditorHint()) AddChild(new MeshInstance3D { Name = \"Grown\", Mesh = new BoxMesh() });\n"
                    "    }\n}\n")
    scene = project / "grower.tscn"
    scene.write_text('[gd_scene format=3]\n\n[ext_resource type="Script" path="res://Grower.cs" id="1_grower"]\n\n'
                     '[node name="Grower" type="Node3D"]\nscript = ExtResource("1_grower")\n')
    try:
        run("editor", "--project", project, "--scene", "res://grower.tscn", "--out", tmp_path / "out")
        report = json.loads((tmp_path / "out" / "report.json").read_text())
        assert report["tree"]["name"] == "Grower"
        assert report["tree"].get("made_by_scripts") == 1
        assert not [e for e in report["open_errors"] + report["errors"] if e["type"] != "warning"]
    finally:
        tool.unlink()
        scene.unlink()


def test_a_start_builds_only_when_the_code_changed(project, tmp_path, monkeypatch):
    """gdh keeps a stamp of what it last built from: an unchanged project starts without dotnet build, a changed .cs
    builds again, and --rebuild builds anyway."""
    calls = tmp_path / "dotnet-calls"
    shim = tmp_path / "bin" / "dotnet"
    shim.parent.mkdir()
    shim.write_text(f'#!/bin/sh\necho "$*" >> {calls}\nexec {shutil.which("dotnet")} "$@"\n')
    shim.chmod(0o755)
    monkeypatch.setenv("PATH", f"{shim.parent}{os.pathsep}{os.environ['PATH']}")

    def builds():
        return calls.read_text().count("build") if calls.exists() else 0

    probe = project / "Probe.cs"
    source = probe.read_text()
    try:
        run("live", "start", "--project", project, "--session", SESSION, "--out", tmp_path / "out")
        built = builds()  # (once, if the tests before changed the code)
        run("live", "restart", "--session", SESSION)
        assert builds() == built
        assert run("live", "eval", "scene.Answer()", "--session", SESSION).stdout.strip() == "42"
        probe.write_text(source.replace("=> 42", "=> 43"))
        run("live", "restart", "--session", SESSION)
        assert builds() == built + 1
        assert run("live", "eval", "scene.Answer()", "--session", SESSION).stdout.strip() == "43"
        run("live", "restart", "--session", SESSION, "--rebuild")
        assert builds() == built + 2
    finally:
        probe.write_text(source)
        run("live", "stop", "--session", SESSION, check=False)
