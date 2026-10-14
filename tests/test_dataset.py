import os
import re
import sqlite3

import numpy as np
import pytest
from dash.exceptions import PreventUpdate

from analyzer import dataset as dataset_module
from analyzer.config import AnalyzerConfig
from analyzer.dataset import get_dataset
from analyzer.graphs.common import COLOR_CODES
from tests.conftest import create_database

ROWS = [
    # id, delay, length, color, response
    (1, 10, 1, "G", b"ok"),
    (2, 20, 1, "Y", b"ok"),
    (3, 30, 1, "Y", b"crash\ndump"),
    (4, 40, 1, "M", b"glitch"),
    (5, 50, 1, "Y", None),
]


@pytest.fixture
def config(tmp_path, make_database, request_from):
    make_database(rows=ROWS)
    with request_from("127.0.0.1"):
        yield AnalyzerConfig(directory=str(tmp_path), database="test.sqlite")


def recolor(dataset, fixgreen=False, flags=0, **regexes):
    """Recolor with the regexes per color code (e.g. R="crash")."""
    return list(dataset.recolor([regexes.get(code) for code in COLOR_CODES], COLOR_CODES, fixgreen, flags))


def test_load(config):
    dataset = get_dataset(config)
    assert list(dataset.df["id"]) == [1, 2, 3, 4, 5]
    assert list(dataset.base_colors) == ["G", "Y", "Y", "M", "Y"]


def test_recolor(config):
    dataset = get_dataset(config)
    assert recolor(dataset) == ["G", "Y", "Y", "M", "Y"]
    assert recolor(dataset, R="crash") == ["G", "Y", "R", "M", "Y"]
    # later fields win (red is the last field)
    assert recolor(dataset, G="ok", R="o") == ["R", "R", "Y", "M", "Y"]
    # empty responses only match regexes that match nothing
    assert recolor(dataset, B="^$") == ["G", "Y", "Y", "M", "B"]


def test_recolor_keeps_green(config):
    dataset = get_dataset(config)
    assert recolor(dataset, fixgreen=True, R="ok") == ["G", "R", "Y", "M", "Y"]
    # an experiment recolored to green is kept green by the next fields too
    assert recolor(dataset, fixgreen=True, G="glitch", R=".") == ["G", "R", "R", "G", "Y"]


def test_recolor_flags(config):
    dataset = get_dataset(config)
    assert recolor(dataset, R="crash.dump") == ["G", "Y", "Y", "M", "Y"]
    assert recolor(dataset, flags=re.DOTALL, R="crash.dump") == ["G", "Y", "R", "M", "Y"]


def test_invalid_regex_is_skipped(config):
    dataset = get_dataset(config)
    assert recolor(dataset, Y="(", R="glitch") == ["G", "Y", "Y", "R", "Y"]


def test_recolor_is_cached_and_read_only(config):
    dataset = get_dataset(config)
    first = dataset.recolor(["ok"] + [None] * 7, COLOR_CODES, False)
    assert dataset.recolor(["ok"] + [None] * 7, COLOR_CODES, False) is first
    with pytest.raises(ValueError):
        first[0] = "R"
    with pytest.raises(ValueError):
        dataset.base_colors[0] = "R"


def test_colors_for_graph_types(config):
    dataset = get_dataset(config)
    config.colors["red"] = ["crash.dump", "crashed"]
    # scatter recolors with DOTALL, scatter2 without; simple doesn't recolor
    config.graph = "scatter"
    assert list(dataset.colors_for(config)) == ["G", "Y", "R", "M", "Y"]
    config.graph = "scatter2"
    assert list(dataset.colors_for(config)) == ["G", "Y", "Y", "M", "Y"]
    config.graph = "simple"
    assert dataset.colors_for(config) is dataset.base_colors


def test_colors_for_fixgreen(config):
    dataset = get_dataset(config)
    config.colors["red"] = [".", None]
    config.fixgreen = True
    assert list(dataset.colors_for(config)) == ["G", "R", "R", "R", "Y"]
    config.fixgreen = False
    assert list(dataset.colors_for(config)) == ["R", "R", "R", "R", "Y"]


def test_datasets_are_cached_per_query(config):
    dataset = get_dataset(config)
    assert get_dataset(config) is dataset
    config.query = "delay > 25"
    filtered = get_dataset(config)
    assert filtered is not dataset
    assert list(filtered.df["id"]) == [3, 4, 5]


def test_bad_query(config):
    config.query = "no_such_column = 1"
    with pytest.raises(PreventUpdate):
        get_dataset(config)


def test_database_without_response_column(tmp_path, make_database, request_from):
    make_database("no-response.sqlite", columns="id integer, delay integer, color text", rows=[(1, 2, "G")])
    with request_from("127.0.0.1"):
        with pytest.raises(PreventUpdate):
            get_dataset(AnalyzerConfig(directory=str(tmp_path), database="no-response.sqlite"))


def test_unknown_database(tmp_path, request_from):
    with request_from("127.0.0.1"):
        with pytest.raises(PreventUpdate):
            get_dataset(AnalyzerConfig(directory=str(tmp_path), database="missing.sqlite"))


def test_empty_result(config):
    config.query = "delay < 0"
    dataset = get_dataset(config)
    assert len(dataset.df) == 0
    assert list(dataset.colors_for(config)) == []
    assert isinstance(dataset.base_colors, np.ndarray)


#
# a database that is still being written to
#


@pytest.fixture
def loads(monkeypatch):
    """The rowids that databases were loaded from (0: all experiments)."""
    calls = []
    original = dataset_module.load_experiments

    def load_experiments(path, query, after=0, responses=None):
        calls.append(after)
        return original(path, query, after, responses)

    monkeypatch.setattr(dataset_module, "load_experiments", load_experiments)
    return calls


def append(config, rows):
    con = sqlite3.connect(os.path.join(config.directory, config.database))
    con.executemany("INSERT INTO experiments VALUES (?, ?, ?, ?, ?)", rows)
    con.commit()
    con.close()


def test_new_experiments_are_added(config, loads):
    dataset = get_dataset(config)
    append(config, [(6, 60, 1, "R", b"ok"), (7, 70, 1, "G", b"new")])
    grown = get_dataset(config)
    assert loads == [0, 5]
    assert list(grown.df["id"]) == [1, 2, 3, 4, 5, 6, 7]
    assert list(grown.base_colors) == ["G", "Y", "Y", "M", "Y", "R", "G"]
    assert grown.df["response"][5] is dataset.df["response"][0]
    assert recolor(grown, R="new") == ["G", "Y", "Y", "M", "Y", "R", "R"]
    # one version per database and query
    assert list(dataset_module._DATASET_CACHE.values()) == [grown]


def test_new_experiments_with_a_query(config, loads):
    config.query = "color = 'R'"
    get_dataset(config)
    append(config, [(6, 60, 1, "R", b"a"), (7, 70, 1, "G", b"b"), (8, 80, 1, "R", b"c")])
    assert list(get_dataset(config).df["id"]) == [6, 8]
    append(config, [(9, 90, 1, "G", b"d")])
    assert list(get_dataset(config).df["id"]) == [6, 8]
    # (nothing matched at first, so the second load starts at the beginning as well)
    assert loads == [0, 0, 8]


def test_a_replaced_database_is_loaded_again(config, loads, tmp_path):
    get_dataset(config)
    create_database(str(tmp_path / "new.sqlite"), "id integer, delay integer, length integer, color text, response blob",
                    [(i, i, 1, "G", b"x") for i in range(100, 108)]).close()
    os.replace(tmp_path / "new.sqlite", tmp_path / "test.sqlite")
    assert list(get_dataset(config).df["id"]) == list(range(100, 108))
    assert loads == [0, 0]


def test_removed_experiments_load_the_database_again(config, loads):
    get_dataset(config)
    con = sqlite3.connect(os.path.join(config.directory, config.database))
    con.execute("DELETE FROM experiments WHERE id > 3")
    con.commit()
    con.close()
    assert list(get_dataset(config).df["id"]) == [1, 2, 3]
    assert loads == [0, 0]


def test_caches_are_emptied_when_memory_runs_out(config, monkeypatch, make_database):
    from analyzer import database, table

    make_database("other.sqlite", rows=ROWS)
    other = AnalyzerConfig(directory=config.directory, database="other.sqlite")
    table.get_table(get_dataset(other), other, True, None, None)
    # not enough memory, until the caches are emptied
    monkeypatch.setattr(database, "available_memory",
                        lambda: 2**40 if not (dataset_module._DATASET_CACHE or table._TABLE_CACHE) else 0)
    assert len(get_dataset(config).df) == 5
    assert list(dataset_module._DATASET_CACHE) == [(os.path.join(config.directory, "test.sqlite"), "")]
    assert not table._TABLE_CACHE
    # still not enough after emptying the caches: the database really doesn't fit
    monkeypatch.setattr(database, "available_memory", lambda: 0)
    config.query = "delay > 1"
    with pytest.raises(dataset_module.DatasetError, match="not enough memory"):
        get_dataset(config)
