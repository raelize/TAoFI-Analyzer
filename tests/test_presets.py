"""The presets of the command line and the environment (gunicorn) end up in the page."""
import json
import os
import subprocess
import sys
import time
import urllib.request

from tests.conftest import EXAMPLE_DATABASE, EXAMPLE_DIRECTORY, REPO, free_port

AXES = {"x": "delay", "y": "length", "z": "id", "x2": "id", "y2": "delay"}


def config_store(layout):
    """The initial config-store in the page layout."""
    if isinstance(layout, dict):
        props = layout.get("props")
        if isinstance(props, dict) and props.get("id") == "config-store":
            return props["data"]
        layout = list(layout.values())
    if isinstance(layout, list):
        for child in layout:
            found = config_store(child)
            if found is not None:
                return found
    return None


def test_command_line():
    port = free_port()
    arguments = [f"--{axis}={value}" for axis, value in AXES.items()]
    process = subprocess.Popen([sys.executable, "taofi-analyzer.py", EXAMPLE_DIRECTORY, "--port", str(port),
                                "--database", EXAMPLE_DATABASE, "--graph", "scatter2", *arguments], cwd=REPO,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                layout = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/_dash-layout"))
                break
            except OSError:
                time.sleep(0.1)
        store = config_store(layout)
    finally:
        process.terminate()
        process.wait()
    assert (store["database"], store["graph"]) == (EXAMPLE_DATABASE, "scatter2")
    assert {axis: store[axis] for axis in AXES} == AXES


def run_with(environment):
    """The initial config-store when started with ``environment`` (as gunicorn does)."""
    script = "import importlib, json; from dataclasses import asdict; importlib.import_module('taofi-analyzer'); " \
             "from analyzer.config import server_config; print(json.dumps(asdict(server_config)))"
    environment = dict(os.environ, ANALYZER_DIRECTORY=EXAMPLE_DIRECTORY, **environment)
    return subprocess.run([sys.executable, "-c", script], cwd=REPO, env=environment, capture_output=True, text=True)


def test_environment():
    output = run_with({"ANALYZER_DATABASE": EXAMPLE_DATABASE, "ANALYZER_GRAPH": "3d",
                       **{f"ANALYZER_{axis.upper()}": value for axis, value in AXES.items()}})
    assert output.returncode == 0, output.stderr
    store = json.loads(output.stdout.strip().splitlines()[-1])
    assert (store["database"], store["graph"]) == (EXAMPLE_DATABASE, "3d")
    assert {axis: store[axis] for axis in AXES} == AXES


def test_defaults():
    output = run_with({})
    store = json.loads(output.stdout.strip().splitlines()[-1])
    assert store["graph"] == "scatter"
    assert {axis: store[axis] for axis in AXES} == dict.fromkeys(AXES)


def test_unknown_graph_type():
    output = run_with({"ANALYZER_GRAPH": "nope"})
    assert output.returncode != 0
    assert "Unknown graph type 'nope' (choose from scatter, scatter2, 3d, mega)" in output.stderr
