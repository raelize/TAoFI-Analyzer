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
                                "--database", EXAMPLE_DATABASE, *arguments], cwd=REPO,
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
    assert store["database"] == EXAMPLE_DATABASE
    assert {axis: store[axis] for axis in AXES} == AXES


def test_environment():
    environment = dict(os.environ, ANALYZER_DIRECTORY=EXAMPLE_DIRECTORY, ANALYZER_DATABASE=EXAMPLE_DATABASE,
                       **{f"ANALYZER_{axis.upper()}": value for axis, value in AXES.items()})
    script = "import importlib, json; from dataclasses import asdict; importlib.import_module('taofi-analyzer'); " \
             "from analyzer.config import server_config; print(json.dumps(asdict(server_config)))"
    output = subprocess.run([sys.executable, "-c", script], cwd=REPO, env=environment, capture_output=True, text=True, check=True)
    store = json.loads(output.stdout.strip().splitlines()[-1])
    assert store["database"] == EXAMPLE_DATABASE
    assert {axis: store[axis] for axis in AXES} == AXES
