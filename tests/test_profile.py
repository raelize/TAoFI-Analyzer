"""How long the analyzer takes to open databases of increasing size.

Opt-in: uv run pytest -m profile -s
(-s shows the timings as they come; they're also in the summary at the end)

The databases are generated once and kept in .pytest_cache/d/profile-databases
(1 000 000 experiments with 4096 byte responses is about 4 GB). Settings:
  ANALYZER_PROFILE_SIZES     numbers of experiments (default 1000,10000,100000,1000000)
  ANALYZER_PROFILE_RESPONSE  bytes per response (default 4096)
A size the analyzer refuses to load (not enough memory) is skipped, as is
generating one without enough disk space.
"memory" is the largest the test process got so far (the sizes go up).
"""

import os
import random
import resource
import shutil
import sqlite3
import time

import plotly.io
import pytest

from analyzer.config import COLORS, AnalyzerConfig
from analyzer.database import get_number_of_experiments
from analyzer.dataset import DatasetError, get_dataset
from analyzer.graphs import GRAPHS
from analyzer.table import get_table

pytestmark = pytest.mark.profile

SIZES = [int(s) for s in os.environ.get("ANALYZER_PROFILE_SIZES", "1000,10000,100000,1000000").split(",")]
RESPONSE_SIZE = int(os.environ.get("ANALYZER_PROFILE_RESPONSE", "4096"))

# like a real campaign: most responses are one of a few, every 10th is unique (e.g. a crash dump)
COMMON_RESPONSES = 8
UNIQUE_EVERY = 10

RESULTS = []


def generate(path, experiments):
    """An analyzer database: id, delay, length, color, response (written to a temporary file first)."""
    rng = random.Random(experiments)
    common = [rng.randbytes(RESPONSE_SIZE) for _ in range(COMMON_RESPONSES)]

    def rows():
        for i in range(experiments):
            if i % UNIQUE_EVERY == 0:
                response = i.to_bytes(8, "little") + common[0][8:]
                color = "R"
            else:
                response = common[i % COMMON_RESPONSES]
                color = "GGGGYYMO"[i % COMMON_RESPONSES]
            yield i, rng.randrange(1000, 2000), rng.randrange(1000, 5000), color, response

    partial = path + ".partial"
    if os.path.exists(partial):
        os.remove(partial)
    con = sqlite3.connect(partial)
    con.execute("CREATE TABLE experiments(id integer, delay integer, length integer, color text, response blob)")
    con.execute("CREATE TABLE metadata(argv text)")
    con.execute("INSERT INTO metadata VALUES (?)", (f"--profile {experiments}",))
    con.executemany("INSERT INTO experiments VALUES (?, ?, ?, ?, ?)", rows())
    con.commit()
    con.close()
    os.replace(partial, path)


@pytest.fixture(scope="module")
def directory(request):
    return str(request.config.cache.mkdir("profile-databases"))


@pytest.fixture(scope="module", autouse=True)
def summary(request):
    yield
    if not RESULTS:
        return
    steps = list(RESULTS[0][1])
    lines = [
        "",
        f"load times in seconds (responses of {RESPONSE_SIZE} bytes)",
        f"{'experiments':>12}" + "".join(f"{step:>14}" for step in steps),
    ]
    for experiments, timings in RESULTS:
        lines.append(f"{experiments:>12}" + "".join(f"{timings.get(step, ''):>14}" for step in steps))
    request.config.pluginmanager.get_plugin("terminalreporter").write_line("\n".join(lines))


@pytest.mark.parametrize("experiments", SIZES, ids=str)
def test_open_database(experiments, directory, request_from, capsys):
    database = f"profile-{experiments}-{RESPONSE_SIZE}.sqlite"
    path = os.path.join(directory, database)
    timings = {}

    def step(name, func):
        started = time.perf_counter()
        result = func()
        timings[name] = f"{time.perf_counter() - started:.3f}"
        return result

    if not os.path.exists(path):
        needed = experiments * (RESPONSE_SIZE + 100)
        if shutil.disk_usage(directory).free < needed * 1.1:
            pytest.skip(f"generating it needs about {needed / 2**30:.1f} GB of disk space")
        step("generate", lambda: generate(path, experiments))
    else:
        timings["generate"] = "(cached)"

    config = AnalyzerConfig(directory=directory, database=database, x="delay", y="length", z="id", x2="id", y2="delay")
    # a recolor regex, so that recoloring is part of the graph
    config.colors["green"] = ["^\\x00", None]
    regexes = [config.colors[color][0] for color in COLORS]

    with request_from("127.0.0.1"):
        # the database list
        assert step("count", lambda: get_number_of_experiments(directory, database)) == experiments
        # the graph tab: load, draw and send
        try:
            dataset = step("load", lambda: get_dataset(config))
        except DatasetError as e:
            pytest.skip(str(e))
        assert len(dataset.df) == experiments
        # every graph type: drawn, and as sent to the browser
        for graph in GRAPHS:
            config.graph = graph
            draw = GRAPHS[graph]
            figure = step(
                graph,
                lambda draw=draw: draw(
                    config.x, config.y, config.fixgreen, dataset, config, COLORS, *regexes, *[None] * len(COLORS)
                ),
            )
            step(f"{graph} json", lambda figure=figure: plotly.io.to_json(figure))
        config.graph = "scatter"
        # the data tab: the table and its first page, squeezed (default) and not
        squeezed = step("data", lambda: get_table(dataset, config, True, None, None))
        step("data page", lambda: squeezed.page(0, 100, [], {}))
        table = step("data all", lambda: get_table(dataset, config, False, None, None))
        rows, count = step("data all page", lambda: table.page(0, 100, [], {}))
        assert count == experiments
        # the grid's filter on the response column decodes all responses
        step(
            "data filter",
            lambda: table.page(0, 100, [], {"response": {"filterType": "text", "type": "contains", "filter": "x"}}),
        )
        # again (e.g. another browser tab): from the cache
        step("load cached", lambda: get_dataset(config))
    timings["memory (MB)"] = str(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024)

    RESULTS.append((experiments, timings))
    with capsys.disabled():
        print(f"\n{experiments} experiments: " + ", ".join(f"{k} {v}" for k, v in timings.items()))
