"""gdh live's agent loop against testbed/chatty/chatty.tscn: step --until and --trace, batch, problems on stderr and
--strict, the game's output and a script's backtrace in replies, list, and options run together in one argument.

Chatty counts physics frames (ticks) and the frames ui_right is held (right_frames), prints "tick N" every 10 frames,
prints on request (say, repeat), and raises an error two calls down (fail).
"""
import csv
import json
import os
import subprocess
import sys

import pytest

from conftest import TESTBED, gdh, gdh_json

SESSION = f"test-loop-{os.getpid()}"


@pytest.fixture(scope="module")
def chatty(tmp_path_factory, display):
    out = tmp_path_factory.mktemp("loop")
    started = gdh("live", "start", "--project", TESTBED, "--scene", "res://chatty/chatty.tscn",
                  "--session", SESSION, "--out", out)
    yield started
    gdh("live", "stop", "--session", SESSION)


def live(*args):
    return gdh_json("live", *args, "--session", SESSION)


def value(expr):
    return live("eval", expr)["result"]["value"]


def run(*args, stdin=None, env=None):
    """gdh live ARGS on the session, unchecked, with stdin and extra environment."""
    return subprocess.run([sys.executable, "-m", "gdh", "live", *args, "--session", SESSION], input=stdin,
                          capture_output=True, text=True, timeout=600, env={**os.environ, **(env or {})})


def test_until_stops_at_the_first_check_that_holds(chatty):
    ticks = value("ticks")
    reply = live("step", "--until", f"ticks >= {ticks + 17}")
    assert reply["result"]["frames"] == 17
    until = reply["result"]["until"]
    assert until["met"] and until["value"] is True
    assert until["frame"] == reply["frame"] == reply["result"]["status"]["frame"]
    # Checked every 5 frames: the first check that holds is at 20.
    ticks = value("ticks")
    reply = live("step", "--until", f"ticks >= {ticks + 17}", "--every", "5")
    assert reply["result"]["frames"] == 20 and reply["result"]["until"]["checks"] == 4


def test_until_gives_up_at_max_with_exit_1(chatty):
    proc = run("step", "--until", "ticks < 0", "--max", "12")
    assert proc.returncode == 1
    assert "stepped 12 frames" in proc.stdout
    assert "--until ticks < 0 didn't hold in 12 frames: it was false" in proc.stderr
    proc = run("step", "30", "--until", "get_node('Nope').visible", "--json")
    assert proc.returncode == 1
    until = json.loads(proc.stdout)["result"]["until"]
    assert not until["met"] and until["checks"] == 30 and "error" in until
    proc = run("step", "3", "--until", "ticks < 0", "--every", "5")
    assert proc.returncode == 1 and "never checked, as --every is more than that" in proc.stderr


def test_until_lets_go_of_a_hold_when_it_ends_early(chatty):
    held = value("right_frames")
    reply = live("step", "--until", f"right_frames >= {held + 10}", "--hold", "ui_right", "--max", "300")
    assert reply["result"]["frames"] == 10
    live("step", "5")
    assert value("right_frames") == held + 10


def test_trace_gives_each_checked_frame_and_writes_csv(chatty, tmp_path):
    start = value("ticks")
    out = tmp_path / "trace.csv"
    reply = live("step", "30", "--trace", "ticks", "--trace", "ticks % 2 == 0", "--every", "10", "--trace-out", out)
    trace = reply["result"]["trace"]
    assert trace["exprs"] == ["ticks", "ticks % 2 == 0"] and trace["every"] == 10
    frame = reply["frame"] - 30
    assert trace["rows"] == [[frame + k, start + k, (start + k) % 2 == 0] for k in (10, 20, 30)]
    with open(out, newline="") as f:
        assert list(csv.reader(f)) == [["frame", "ticks", "ticks % 2 == 0"],
                                       *[[str(r[0]), str(r[1]), json.dumps(r[2])] for r in trace["rows"]]]


def test_trace_as_text_leaves_out_rows_that_repeat(chatty):
    proc = run("step", "40", "--trace", "right_frames", "--trace", "get_node('Nope').x")
    assert proc.returncode == 0
    rows = [line for line in proc.stdout.splitlines() if line.startswith("  ")]
    assert len(rows) == 2  # the first and the last: nothing changed between
    assert "2 of 40 rows: the rest repeat the row before" in proc.stdout
    assert "trace get_node('Nope').x failed (its value is null)" in proc.stderr


def test_until_and_trace_through_pipe(chatty):
    ticks = value("ticks")
    request = {"cmd": "step", "args": {"frames": 100, "until": f"ticks >= {ticks + 6}", "trace": ["ticks"], "every": 2}}
    # One expression alone may come as a string, and null is no expression.
    loose = {"cmd": "step", "args": {"frames": 2, "until": None, "trace": "ticks"}}
    proc = run("pipe", stdin=json.dumps(request) + "\n" + json.dumps(loose) + "\n")
    result, other = [json.loads(line)["result"] for line in proc.stdout.splitlines()]
    assert result["frames"] == 6 and result["until"]["met"]
    assert [row[1] for row in result["trace"]["rows"]] == [ticks + 2, ticks + 4, ticks + 6]
    assert "until" not in other and [row[1] for row in other["trace"]["rows"]] == [ticks + 7, ticks + 8]


def test_batch_runs_command_lines_in_one_process(chatty):
    ticks = value("ticks")
    lines = ["# set up", "step 5", "", "gdh live eval ticks", f"step --until 'ticks >= {ticks + 12}'", "eval 'ticks'"]
    proc = run("batch", stdin="\n".join(lines) + "\n")
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout.splitlines()
    assert [line for line in out if line.startswith("> ")] == [
        "> step 5", "> gdh live eval ticks", f"> step --until 'ticks >= {ticks + 12}'", "> eval 'ticks'"]
    assert out[out.index("> gdh live eval ticks") + 1] == str(ticks + 5)
    assert out[-1] == str(ticks + 12)


def test_batch_goes_on_after_an_error_or_stops_when_asked(chatty):
    lines = "eval 1\neval 'nonsense('\nstep 2 --hold\neval 2\n"
    proc = run("batch", stdin=lines)
    assert proc.returncode == 1
    assert proc.stdout.splitlines()[-1] == "2"
    assert "live step: argument --hold: expected one argument" in proc.stderr
    assert "2 of 4 commands failed (lines 2, 3)" in proc.stderr
    proc = run("batch", "--stop-on-error", stdin=lines)
    assert proc.returncode == 1
    assert "> eval 2" not in proc.stdout
    assert "it stopped at line 2" in proc.stderr


def test_batch_json_is_a_line_per_command(chatty):
    proc = run("batch", "--json", stdin="eval 1+1\neval 'nonsense('\n")
    first, second = [json.loads(line) for line in proc.stdout.splitlines()]
    assert first["ok"] and first["line"] == "eval 1+1" and first["replies"][0]["result"]["value"] == 2
    assert not second["ok"] and second["error"] == "Expected expression."


def test_problems_go_to_stderr_and_results_to_stdout(chatty):
    proc = run("eval", "get_node('Missing')")
    assert proc.returncode == 0 and proc.stdout == "null\n"
    assert "error: Node not found" in proc.stderr
    proc = run("eval", "ResourceLoader.load('res://nope/missing.png')")
    assert proc.stdout == "null\n"
    assert "DEFECT: 1 resource(s) failed to load (res://nope/missing.png)" in proc.stderr
    run("eval", "tree.set_pause(false)")
    proc = run("status")
    assert "note: The game unpaused itself" in proc.stderr and "unpaused" not in proc.stdout


def test_strict_fails_a_command_that_raised_engine_errors(chatty):
    proc = run("eval", "get_node('Missing')", "--strict")
    assert proc.returncode == 1 and proc.stdout == "null\n"
    assert "--strict: the game raised 1 engine error during the command" in proc.stderr
    assert run("eval", "get_node('Missing')", env={"GDH_STRICT": "1"}).returncode == 1
    assert run("eval", "1", env={"GDH_STRICT": "1"}).returncode == 0
    proc = run("batch", "--strict", stdin="eval 1\neval get_node('Missing')\neval 2\n")
    assert proc.returncode == 1 and "1 of 3 commands failed (line 2)" in proc.stderr


def test_replies_carry_what_the_game_printed(chatty):
    ticks = value("ticks")
    reply = live("step", "20")
    assert reply["output"] == [f"tick {t}" for t in range(ticks + 1, ticks + 21) if t % 10 == 0]
    assert run("step", "10").stderr.startswith("game: tick ")
    # Repeats merge, and past 40 lines the first 10 and the last 30 are kept.
    assert live("eval", "repeat(50)")["output"] == ["again (x50)"]
    reply = live("eval", "say(100)")
    assert reply["output_cut"] == 60
    assert reply["output"] == [*[f"line {i}" for i in range(10)], "... 60 lines cut ...",
                               *[f"line {i}" for i in range(70, 100)]]
    assert "output" not in live("status")


def test_the_game_s_output_while_loading_is_shown_at_start(chatty):
    assert "game: chatty ready" in chatty.stderr


def test_script_errors_carry_the_script_s_backtrace(chatty):
    error = live("eval", "fail()")["errors"][0]
    assert error["message"] == "chatty failed on purpose"
    # The game's own frames, innermost first, and none of gdh's harness.
    assert [frame.split(" in ")[1] for frame in error["backtrace"]] == ["_inner", "_outer", "fail"]
    assert all(frame.startswith("res://chatty/chatty.gd:") for frame in error["backtrace"])
    assert "  at res://chatty/chatty.gd:" in run("eval", "fail()").stderr
    # An engine error from eval itself has no script to trace.
    assert "backtrace" not in live("eval", "get_node('Missing')")["errors"][0]


def test_list_shows_every_session(chatty, display):
    from gdh.live import session_path
    rows = json.loads(gdh("live", "list", "--json").stdout)
    row = next(r for r in rows if r["name"] == SESSION)
    assert row["state"] == "running"
    assert row["project"] == str(TESTBED.resolve()) and row["scene"] == "res://chatty/chatty.tscn"
    assert row["pids"] == [json.loads(session_path(SESSION).read_text())["pid"]]
    assert row["displays"][0]["kind"] == display
    assert 0 <= row["age_s"] < 600 and row["idle_s"] >= 0
    text = gdh("live", "list").stdout
    assert any(line.startswith(f"{SESSION}: running, pid {row['pids'][0]}, display {display}")
               for line in text.splitlines())


def test_options_run_together_in_one_argument_get_a_hint():
    proc = gdh("live", "step", "10", f"--session {SESSION} --json", check=False)
    assert proc.returncode == 2
    assert proc.stderr.count("\n") == 1 and "one argument holding spaces" in proc.stderr
    # An option's value with spaces, after =, is no such thing.
    proc = gdh("live", "eval", "1", "--session=no such session", check=False)
    assert "No live session" in proc.stderr
