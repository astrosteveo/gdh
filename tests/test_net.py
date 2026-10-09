"""gdh live start --net and gdh live net: two instances of testbed/live/net.tscn talking to an echo server
(testbed/live/echo.py) over UDP and TCP through gdh's network proxy. The games run in real time between checks, as a
network's delays are in real time (netem.py)."""
import asyncio
import json
import os
import sys
import time

import pytest

from conftest import TESTBED, gdh, gdh_json

SESSION = f"test-net-{os.getpid()}"
ECHO = TESTBED / "live" / "echo.py"


@pytest.fixture(scope="module")
def pair(tmp_path_factory, display):
    out = tmp_path_factory.mktemp("net")
    gdh("live", "start", "--project", TESTBED, "--scene", "res://live/net.tscn", "--session", SESSION, "--out", out,
        "--instances", "2", "--companion", f"echo={sys.executable} {ECHO} {{port}}", "--net", "echo",
        "--", "--port", "{echo.port}")
    yield out
    gdh("live", "stop", "--session", SESSION)


def counts(seconds=1.0):
    """Each instance's [UDP replies, TCP bytes back, last round trip ms, TCP connects, TCP drops] over `seconds` of
    real time."""
    gdh("live", "eval", "reset()", "--instance", "all", "--session", SESSION)
    gdh("live", "run", "--session", SESSION)
    time.sleep(seconds)
    gdh("live", "pause", "--session", SESSION)
    reply = gdh_json("live", "eval", "[udp_replies, tcp_replies, last_rtt_ms, tcp_connects, tcp_drops]",
                     "--instance", "all", "--session", SESSION)
    return [part["result"]["value"] for part in reply["instances"]]


def net(*args):
    return gdh_json("live", "net", *args, "--session", SESSION)["routes"]


def test_each_instance_gets_a_port_of_its_own_on_the_proxy(pair):
    routes = net()
    assert [r["instance"] for r in routes] == [0, 1]
    ports = [r["port"] for r in routes]
    assert len(set(ports)) == 2 and routes[0]["target"] == routes[1]["target"] not in ports
    assert gdh_json("live", "eval", "port", "--instance", "all", "--session", SESSION)["instances"][1]["result"][
        "value"] == ports[1]
    first, second = counts()
    assert first[0] > 5 and first[1] > 5 and second[0] > 5 and second[1] > 5
    assert all(r["udp_up"] > 0 and r["tcp_bytes_down"] > 0 for r in net())


def test_latency_on_one_instance_delays_only_its_round_trips(pair):
    net("--latency", "120", "--instance", "1")
    try:
        first, second = counts(1.5)
        assert second[2] >= 240  # 120 ms each way
        assert 0 <= first[2] < 120
    finally:
        net("--latency", "0")


def test_a_cut_link_passes_nothing_until_healed_and_a_reset_makes_the_game_reconnect(pair):
    net("--cut", "--instance", "1")
    try:
        counts(0.3)  # what was on its way before the cut
        first, second = counts(1.0)
        assert first[0] > 5 and second[0] == 0 and second[1] == 0
        cut = net("--instance", "1")[1]
        assert cut["cut"] and cut["udp_dropped"] > 0
    finally:
        net("--heal", "--instance", "1")
    assert counts(1.0)[1][0] > 5
    connects = counts(0.2)[1][3]
    [state] = [r for r in net("--reset", "--instance", "1") if r["instance"] == 1]
    assert state["tcp_open"] == 0
    after = counts(1.0)[1]
    assert after[4] >= 1 and after[3] == connects + 1 and after[1] > 0  # dropped, connected again, talking


def test_net_refuses_a_session_without_a_proxy_and_bad_values(pair):
    proc = gdh("live", "net", "--loss", "120", "--session", SESSION, check=False)
    assert proc.returncode == 1 and "a percent from 0 to 100" in proc.stderr
    proc = gdh("live", "net", "--session", "no-such-session", check=False)
    assert proc.returncode == 1
    proc = gdh("live", "start", "--project", TESTBED, "--session", f"{SESSION}-x", "--net-loss", "5", check=False)
    assert proc.returncode == 1 and "go with --net NAME" in proc.stderr


def test_loss_drops_the_same_datagrams_with_the_same_seed():
    import random
    from gdh.netem import Route

    async def pattern(seed):
        route = Route({"name": "s", "instance": 0, "listen": 0, "target": 0, "loss": 50},
                      random.Random(random.Random(seed).random()))
        return [route.dropped() for _ in range(200)]

    one, two, other = (asyncio.run(pattern(s)) for s in (7, 7, 8))
    assert one == two != other and 60 < sum(one) < 140
