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
