"""gdh with a C# project (testbed_cs): it builds the assemblies with dotnet
first, runs Godot's .NET build (godot-mono) without being told, and reports
a failed build in a sentence."""
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
