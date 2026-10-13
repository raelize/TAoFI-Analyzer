"""Shared fixtures: test databases, the app and a client for its callbacks."""
import importlib
import os
import sqlite3
from contextlib import contextmanager

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMPLE_DIRECTORY = os.path.join(REPO, "databases")
EXAMPLE_DATABASE = "g_CharacterizeRandom.py_20231019_204746.sqlite"

# the example database (see test_callbacks.py)
EXAMPLE_EXPERIMENTS = 6843
EXAMPLE_ARGV = "--target /dev/ttyUSB1 --delay 1000 2000 --length 1000 5000"

# importing the app configures it from the environment (as gunicorn does)
os.environ.setdefault("ANALYZER_DIRECTORY", EXAMPLE_DIRECTORY)


def create_database(path, columns, rows=(), argv=None, wal=False):
    """Create an experiments database: ``columns`` is the column list of the table."""
    con = sqlite3.connect(path)
    if wal:
        con.execute("PRAGMA journal_mode=WAL")
    con.execute(f"CREATE TABLE experiments({columns})")
    if rows:
        placeholders = ", ".join("?" * len(rows[0]))
        con.executemany(f"INSERT INTO experiments VALUES ({placeholders})", rows)
    if argv is not None:
        con.execute("CREATE TABLE metadata(argv text)")
        con.execute("INSERT INTO metadata VALUES (?)", (argv,))
    con.commit()
    return con


@pytest.fixture
def make_database(tmp_path):
    """Factory for test databases in a temporary directory; returns the file name."""

    def make(name="test.sqlite", columns="id integer, delay integer, length integer, color text, response blob", rows=(), **kwargs):
        create_database(str(tmp_path / name), columns, rows, **kwargs).close()
        return name

    return make


@pytest.fixture(scope="session")
def app_module():
    return importlib.import_module("taofi-analyzer")


@pytest.fixture(autouse=True)
def clean_state(app_module):
    """Every test starts with empty caches and the example directory."""
    from analyzer import database, dataset, table
    from analyzer.config import server_config

    directory = server_config.directory
    database._COUNT_CACHE.clear()
    dataset._DATASET_CACHE.clear()
    table._TABLE_CACHE.clear()
    yield
    server_config.directory = directory


@pytest.fixture
def request_from(app_module):
    """Context manager: code runs as if handling a request from ``address``."""

    @contextmanager
    def context(address="127.0.0.1"):
        with app_module.server.test_request_context(environ_base={"REMOTE_ADDR": address}):
            yield

    return context


class DashClient:
    """Calls the app's callbacks the way the browser does (POST /_dash-update-component)."""

    def __init__(self, app_module, address):
        self.client = app_module.server.test_client()
        self.client.environ_base["REMOTE_ADDR"] = address
        self.client.get("/")
        self.dependencies = self.client.get("/_dash-dependencies").get_json()

    def layout_store(self):
        """The initial config-store of the page."""

        def find(node):
            if isinstance(node, dict):
                props = node.get("props")
                if isinstance(props, dict) and props.get("id") == "config-store":
                    return props["data"]
                nodes = node.values()
            elif isinstance(node, list):
                nodes = node
            else:
                return None
            for child in nodes:
                found = find(child)
                if found is not None:
                    return found

        return find(self.client.get("/_dash-layout").get_json())

    def callback(self, output):
        """The callback whose output key contains ``output``."""
        return next(d for d in self.dependencies if output in d["output"])

    def call(self, output, values, triggered=None, ids=None):
        """Call a callback.

        values: {"<id>.<property>": value} for its inputs and states.
        ids: the concrete ids of pattern-matching inputs/outputs, keyed by their
             pattern "<id>.<property>" (a single id, or a list of (id, value)).
        Returns (status code, response); the response maps "<id>" -> {property: value}.
        """
        callback = self.callback(output)
        ids = ids or {}

        def spec(item):
            key = f"{item['id']}.{item['property']}"
            if item["id"].startswith("{"):
                concrete = ids[key]
                if isinstance(concrete, list):
                    return [{"id": i, "property": item["property"], "value": v} for i, v in concrete]
                return {"id": concrete[0], "property": item["property"], "value": concrete[1]}
            return {"id": item["id"], "property": item["property"], "value": values.get(key)}

        outputs = []
        for key in callback["output"].strip(".").split("..."):
            component, prop = key.rsplit(".", 1)
            prop = prop.rstrip("@")
            if component.startswith("{"):
                concrete = ids[f"{component}.{prop}"]
                if isinstance(concrete, list):
                    # the same list of (id, value) as for the inputs/states
                    outputs.append([{"id": i, "property": prop} for i, _ in concrete])
                else:
                    outputs.append({"id": concrete, "property": prop})
            else:
                outputs.append({"id": component, "property": prop})
        body = {
            "output": callback["output"],
            "outputs": outputs if callback["output"].startswith("..") else outputs[0],
            "inputs": [spec(i) for i in callback["inputs"]],
            "state": [spec(s) for s in callback["state"]],
            "changedPropIds": [triggered] if triggered else [],
        }
        response = self.client.post("/_dash-update-component", json=body)
        if response.status_code != 200:
            return response.status_code, None
        return 200, response.get_json()["response"]


@pytest.fixture
def dash_client(app_module):
    """Factory: a client for the app's callbacks, from ``address``."""

    def make(address="127.0.0.1"):
        return DashClient(app_module, address)

    return make
