import numpy as np
import pytest

from analyzer.config import AnalyzerConfig
from analyzer.dataset import get_dataset
from analyzer.graphs.mega import parameters


def test_mega_graph_parameters(tmp_path, make_database, request_from):
    columns = "id integer, delay integer, voltage real, empty integer, note text, color text, response blob"
    make_database(columns=columns, rows=[(1, 10, 1.5, None, "a", "G", b"x"), (2, 20, 2.5, None, "b", "R", b"y")])
    with request_from("127.0.0.1"):
        dataset = get_dataset(AnalyzerConfig(directory=str(tmp_path), database="test.sqlite"))
    # no text or empty columns
    assert parameters(dataset) == ["id", "delay", "voltage"]
    # chosen: in that order, as far as they are number columns
    assert parameters(dataset, ["voltage", "note", "id", "empty", "nope"]) == ["voltage", "id"]


def test_jitter_stays_the_same(tmp_path, make_database, request_from):
    from analyzer.graphs.common import axis_values
    from tests.test_dataset import append

    make_database(rows=[(i, 100, 10, "G", b"x") for i in range(5)])
    config = AnalyzerConfig(directory=str(tmp_path), database="test.sqlite")
    with request_from("127.0.0.1"):
        delay = axis_values(get_dataset(config), "delay", 2)
        assert list(axis_values(get_dataset(config), "delay", 2)) == list(delay)
        assert len(set(delay)) == 5
        # (another sequence for another column)
        assert list(axis_values(get_dataset(config), "length", 2) - 10) != list(delay - 100)
        # new experiments: the others keep their jitter
        append(config, [(5, 100, 10, "G", b"x")])
        assert list(axis_values(get_dataset(config), "delay", 2)[:5]) == list(delay)


def test_sample():
    from analyzer.graphs.common import sample

    colors = np.array(["G"] * 99_000 + ["Y"] * 900 + ["R"] * 100, dtype=object)
    assert sample(colors, 100_000) is None and sample(colors, None) is None
    shown = sample(colors, 10_000)
    # the small classes completely, the large one thinned out
    assert shown[99_000:].all()
    assert 8_500 < shown[:99_000].sum() < 9_500
    # the same experiments every time; new ones at the end don't change it much
    assert (sample(colors, 10_000) == shown).all()
    more = np.concatenate([colors, np.array(["G"] * 100, dtype=object)])
    assert (sample(more, 10_000)[:100_000] <= shown).all()


def test_3d_graph_draws_a_sample(tmp_path, make_database, request_from, monkeypatch):
    from analyzer.graphs import common, scatter3d

    monkeypatch.setitem(common.MAX_POINTS, "3d", 60)
    make_database(rows=[(i, i, i, "G" if i < 490 else "R", b"x") for i in range(500)])
    config = AnalyzerConfig(directory=str(tmp_path), database="test.sqlite", x="delay", y="length", z="id", graph="3d")
    with request_from("127.0.0.1"):
        figure = scatter3d.scatter3d_func("delay", "length", True, get_dataset(config), config, [], *[None] * 16)
    drawn = [len(trace.x) for trace in figure.data]
    assert drawn[1] == 10 and 30 < drawn[0] < 75
    assert figure.layout.legend.title.text == f"Classification ( 500; {sum(drawn)} shown ):"
    # the counts in the legend: all experiments
    assert [trace.name for trace in figure.data] == ["green ( 490 / 98.0% )", "red ( 10 / 2.0% )"]


@pytest.mark.parametrize("graph", ["scatter", "scatter2", "mega"])
def test_graphs_draw_a_sample(tmp_path, make_database, request_from, monkeypatch, graph):
    from analyzer.graphs import GRAPHS, common

    monkeypatch.setitem(common.MAX_POINTS, graph, 60)
    make_database(rows=[(i, i, i, "G", b"x") for i in range(500)])
    config = AnalyzerConfig(
        directory=str(tmp_path), database="test.sqlite", x="delay", y="length", x2="id", y2="delay", graph=graph
    )
    with request_from("127.0.0.1"):
        figure = GRAPHS[graph]("delay", "length", True, get_dataset(config), config, [], *[None] * 16)
    drawn = [len(trace.x) if trace.type != "splom" else len(trace.dimensions[0].values) for trace in figure.data]
    assert all(30 < n < 90 for n in drawn)
    assert figure.layout.legend.title.text.startswith("Classification ( 500; ")


def test_max_points(monkeypatch):
    from analyzer.graphs import common
    from analyzer.graphs.common import max_points

    assert max_points(AnalyzerConfig(graph="scatter")) == 200_000
    assert max_points(AnalyzerConfig(graph="3d")) == 100_000
    # --max-points: for every graph type
    monkeypatch.setattr(common, "DEFAULT_MAX_POINTS", 500_000)
    assert max_points(AnalyzerConfig(graph="3d")) == 500_000
    # the field of the graph tab
    assert max_points(AnalyzerConfig(graph="3d", max_points=1000)) == 1000
    # 0: all
    assert max_points(AnalyzerConfig(graph="3d", max_points=0)) is None
    monkeypatch.setattr(common, "DEFAULT_MAX_POINTS", 0)
    assert max_points(AnalyzerConfig(graph="scatter")) is None


def test_graph_with_max_points(tmp_path, make_database, request_from):
    from analyzer.graphs import GRAPHS

    make_database(rows=[(i, i, i, "G", b"x") for i in range(500)])
    with request_from("127.0.0.1"):
        for limit, drawn in [(50, range(25, 75)), (0, [500]), (None, [500])]:
            config = AnalyzerConfig(
                directory=str(tmp_path), database="test.sqlite", x="delay", y="length", max_points=limit
            )
            figure = GRAPHS["scatter"]("delay", "length", True, get_dataset(config), config, [], *[None] * 16)
            assert len(figure.data[0].x) in drawn
