from analyzer.config import AnalyzerConfig
from analyzer.dataset import get_dataset
from analyzer.graphs.mega import parameters


def test_mega_graph_parameters(tmp_path, make_database, request_from):
    columns = "id integer, delay integer, voltage real, empty integer, note text, color text, response blob"
    make_database(columns=columns, rows=[(1, 10, 1.5, None, "a", "G", b"x"), (2, 20, 2.5, None, "b", "R", b"y")])
    with request_from("127.0.0.1"):
        dataset = get_dataset(AnalyzerConfig(directory=str(tmp_path), database="test.sqlite"))
    # no id, text or empty columns
    assert parameters(dataset) == ["delay", "voltage"]


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

    assert sample(10, 10) is None and sample(10, None) is None
    shown = sample(100_000, 10_000)
    assert 9_000 < shown.sum() < 11_000
    # the same experiments every time; new ones at the end don't change it much
    assert (sample(100_000, 10_000) == shown).all()
    assert (sample(100_100, 10_000)[:100_000] <= shown).all()


def test_3d_graph_draws_a_sample(tmp_path, make_database, request_from, monkeypatch):
    from analyzer.graphs import scatter3d

    monkeypatch.setattr(scatter3d, "MAX_POINTS", 50)
    make_database(rows=[(i, i, i, "GR"[i % 2], b"x") for i in range(500)])
    config = AnalyzerConfig(directory=str(tmp_path), database="test.sqlite", x="delay", y="length", z="id", graph="3d")
    with request_from("127.0.0.1"):
        figure = scatter3d.scatter3d_func("delay", "length", True, get_dataset(config), config, [], *[None] * 16)
    drawn = sum(len(trace.x) for trace in figure.data)
    assert 25 < drawn < 100
    assert figure.layout.legend.title.text == f"Classification ( 500; {drawn} shown ):"
    # the counts in the legend: all experiments
    assert [trace.name for trace in figure.data] == ["green ( 250 / 50.0% )", "red ( 250 / 50.0% )"]
