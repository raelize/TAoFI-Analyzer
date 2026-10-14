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
