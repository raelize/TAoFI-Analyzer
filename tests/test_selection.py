import numpy as np
import pytest

from analyzer.config import AnalyzerConfig
from analyzer.dataset import get_dataset
from analyzer.selection import describe, inside_polygon, selected_rows
from analyzer.table import get_table

# a grid of experiments: delay 0..9, length 0..9
ROWS = [(i, i % 10, i // 10, "G", b"r%d" % (i % 3)) for i in range(100)]


@pytest.fixture
def config(tmp_path, make_database, request_from):
    make_database(rows=ROWS)
    with request_from("127.0.0.1"):
        yield AnalyzerConfig(directory=str(tmp_path), database="test.sqlite", x="delay", y="length")


def selection(config, **outline):
    return {
        "directory": config.directory,
        "database": config.database,
        "query": config.query,
        "jitter": config.jitter,
        "x": "delay",
        "y": "length",
        **outline,
    }


def test_box(config):
    rows = selected_rows(get_dataset(config), config, selection(config, box={"x": [4.5, 2], "y": [0, 1]}))
    assert list(rows) == [2, 3, 4, 12, 13, 14]


def test_lasso(config):
    # a triangle around (0, 0), (4, 0) and (0, 4)
    rows = selected_rows(
        get_dataset(config), config, selection(config, lasso={"x": [-0.5, 4.6, -0.5], "y": [-0.5, -0.5, 4.6]})
    )
    dataset = get_dataset(config)
    points = set(zip(dataset.df["delay"][rows], dataset.df["length"][rows], strict=True))
    assert points == {(x, y) for x in range(5) for y in range(5) if x + y <= 4}


def test_inside_polygon():
    square = (np.array([0, 2, 2, 0]), np.array([0, 0, 2, 2]))
    assert list(inside_polygon(np.array([1, 3, 1, -1]), np.array([1, 1, 3, 1]), *square)) == [True, False, False, False]


def test_with_jitter(config):
    # selected at the positions they were plotted at
    config.jitter = 0.3
    from analyzer.graphs.common import axis_values

    dataset = get_dataset(config)
    x = axis_values(dataset, "delay", 0.3)
    rows = selected_rows(dataset, config, selection(config, box={"x": [-1, 3.5], "y": [-1, 100]}))
    assert list(rows) == list(np.flatnonzero(x <= 3.5))


@pytest.mark.parametrize("other", [{"query": "delay > 1"}, {"jitter": 2}, {"database": "other.sqlite"}, {"x": "nope"}])
def test_selections_of_another_graph_dont_apply(config, other):
    assert (
        selected_rows(get_dataset(config), config, {**selection(config, box={"x": [0, 1], "y": [0, 1]}), **other})
        is None
    )
    assert selected_rows(get_dataset(config), config, None) is None


def test_table_of_a_selection(config):
    box = selection(config, box={"x": [0, 1], "y": [0, 9]})
    dataset = get_dataset(config)
    table = get_table(dataset, config, False, None, None, box)
    assert table.selected == 20
    assert table.page(0, 100, [], {})[1] == 20
    squeezed = get_table(dataset, config, True, None, None, box)
    assert sum(row["amount"] for row in squeezed.page(0, 100, [], {})[0]) == 20
    assert get_table(dataset, config, False, None, None, None).selected is None


def test_describe():
    assert (
        describe({"x": "delay", "y": "length", "box": {"x": [3, 1], "y": [0, 2.5]}}, 7)
        == "the 7 experiments selected in the graph (delay 1–3, length 0–2.5)"
    )
    assert (
        describe({"x": "delay", "y": "length", "lasso": {"x": [], "y": []}}, 1)
        == "the 1 experiments selected in the graph (a lasso on delay and length)"
    )
