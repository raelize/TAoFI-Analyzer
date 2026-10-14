import math

import pandas as pd
import pytest

from analyzer import table as table_module
from analyzer.config import AnalyzerConfig
from analyzer.dataset import get_dataset
from analyzer.table import build_table, get_table, grid_filter_mask, per_response

COLUMNS = "id integer, glitch_delay integer, normal_voltage real, note text, extra blob, empty integer, sometimes real, color text, response blob"
ROWS = [
    # id, glitch_delay, normal_voltage, note, extra, empty, sometimes, color, response
    (1, 10, 1.5, "a", b"\x00\xff", None, None, "G", b"OK"),
    (2, 30, 3.25, "b", b"\x01", None, 1.0, "R", b"FAIL"),
    (3, 20, 2.0, "c", b"\x02", None, 2.0, "G", b"OK"),
    (4, 40, 1.0, "d", b"\x03", None, 3.0, "Y", b"OK"),
]


@pytest.fixture
def dataset(tmp_path, make_database, request_from):
    make_database(columns=COLUMNS, rows=ROWS)
    with request_from("127.0.0.1"):
        yield get_dataset(AnalyzerConfig(directory=str(tmp_path), database="test.sqlite"))


def rows(table, start=0, end=100, sort=(), filters=None):
    return table.page(start, end, list(sort), filters or {})


#
# build_table
#


def test_columns_follow_the_database(dataset):
    table = build_table(dataset, dataset.base_colors, False, None, None)
    # all columns in database order, except the empty one; response columns last
    assert table.columns == ["id", "glitch_delay", "normal_voltage", "note", "extra", "sometimes", "color",
                             "rlen", "response", "hex(response)"]
    assert table.numeric == {"id", "glitch_delay", "normal_voltage", "sometimes"}


def test_rows(dataset):
    table = build_table(dataset, dataset.base_colors, False, None, None)
    page, count = rows(table)
    assert count == 4
    assert page[0] == {
        "id": 1, "glitch_delay": 10, "normal_voltage": 1.5, "note": "a",
        # other blobs are shown as hex
        "extra": "00 ff", "sometimes": None, "color": "G",
        "rlen": 2, "response": "OK", "hex(response)": "4f 4b",
    }


def test_colors_come_from_the_given_colors(dataset):
    colors = dataset.recolor(["OK"] + [None] * 7, ["G", "Y", "M", "O", "C", "B", "Z", "R"], False)
    table = build_table(dataset, colors, False, None, None)
    assert [row["color"] for row in rows(table)[0]] == ["G", "R", "G", "G"]


def test_slicing(dataset):
    table = build_table(dataset, dataset.base_colors, False, 1, 3)
    first = rows(table)[0][1]
    assert (first["response"], first["hex(response)"], first["rlen"]) == ("AI", "41 49", 2)


def test_squeeze(dataset):
    table = build_table(dataset, dataset.base_colors, True, None, None)
    assert table.columns == ["amount", "min(id)", "max(id)", "min(glitch_delay)", "max(glitch_delay)",
                             "min(normal_voltage)", "max(normal_voltage)", "note", "extra",
                             "min(sometimes)", "max(sometimes)", "color", "response", "rlen", "hex(response)"]
    assert table.numeric == {"amount", "min(id)", "max(id)", "min(glitch_delay)", "max(glitch_delay)",
                             "min(normal_voltage)", "max(normal_voltage)", "min(sometimes)", "max(sometimes)"}
    page, count = rows(table)
    assert count == 2
    ok, fail = page
    # most experiments first; numbers as min/max, other columns of the first experiment
    assert ok["amount"] == 3 and fail["amount"] == 1
    assert (ok["min(id)"], ok["max(id)"]) == (1, 4)
    assert (ok["min(glitch_delay)"], ok["max(glitch_delay)"]) == (10, 40)
    assert (ok["min(normal_voltage)"], ok["max(normal_voltage)"]) == (1.0, 2.0)
    assert (ok["min(sometimes)"], ok["max(sometimes)"]) == (2.0, 3.0)
    assert (ok["note"], ok["extra"], ok["color"]) == ("a", "00 ff", "G")
    assert (ok["response"], ok["rlen"], ok["hex(response)"]) == ("OK", 2, "4f 4b")


def test_squeeze_groups_sliced_responses(dataset):
    # "OK"[0:1] == "FAIL"[0:1]? no: "O" vs "F"; [2:] gives "" for OK and "IL" for FAIL
    table = build_table(dataset, dataset.base_colors, True, 2, None)
    page, count = rows(table)
    assert count == 2
    assert {(row["response"], row["amount"]) for row in page} == {("", 3), ("IL", 1)}


def test_squeeze_uses_the_given_colors(dataset):
    colors = dataset.recolor([None] * 7 + ["FAIL"], ["G", "Y", "M", "O", "C", "B", "Z", "R"], False)
    table = build_table(dataset, colors, True, None, None)
    assert {row["response"]: row["color"] for row in rows(table)[0]} == {"OK": "G", "FAIL": "R"}


@pytest.mark.parametrize("squeeze", [False, True])
def test_empty_result(tmp_path, make_database, request_from, squeeze):
    make_database(columns=COLUMNS, rows=ROWS)
    with request_from("127.0.0.1"):
        dataset = get_dataset(AnalyzerConfig(directory=str(tmp_path), database="test.sqlite", query="id < 0"))
    table = build_table(dataset, dataset.base_colors, squeeze, None, None)
    assert rows(table) == ([], 0)
    assert "response" in table.columns


def test_tables_are_cached_per_color_setting(dataset):
    config = AnalyzerConfig(database="test.sqlite")
    first = get_table(dataset, config, False, None, None)
    assert get_table(dataset, config, False, None, None) is first
    config.colors["red"] = ["FAIL", None]
    recolored = get_table(dataset, config, False, None, None)
    assert recolored is not first
    assert [row["color"] for row in rows(recolored)[0]] == ["G", "R", "G", "Y"]


#
# paging, sorting and filtering
#


def test_paging(dataset):
    table = build_table(dataset, dataset.base_colors, False, None, None)
    page, count = rows(table, 1, 3)
    assert count == 4
    assert [row["id"] for row in page] == [2, 3]


def test_sorting(dataset):
    table = build_table(dataset, dataset.base_colors, False, None, None)
    by_delay = rows(table, sort=[{"colId": "glitch_delay", "sort": "desc"}])[0]
    assert [row["id"] for row in by_delay] == [4, 2, 3, 1]
    by_response = rows(table, sort=[{"colId": "response", "sort": "asc"}, {"colId": "id", "sort": "desc"}])[0]
    assert [row["id"] for row in by_response] == [2, 4, 3, 1]
    by_rlen = rows(table, sort=[{"colId": "rlen", "sort": "desc"}])[0]
    assert by_rlen[0]["id"] == 2


def test_filtering(dataset):
    table = build_table(dataset, dataset.base_colors, False, None, None)
    page, count = rows(table, filters={"glitch_delay": {"filterType": "number", "type": "greaterThan", "filter": 15}})
    assert count == 3
    assert [row["id"] for row in page] == [2, 3, 4]
    page, count = rows(table, filters={
        "glitch_delay": {"filterType": "number", "type": "greaterThan", "filter": 15},
        "response": {"filterType": "text", "type": "contains", "filter": "ok"},
    })
    assert [row["id"] for row in page] == [3, 4]
    # filter on hex
    page, count = rows(table, filters={"hex(response)": {"filterType": "text", "type": "startsWith", "filter": "46"}})
    assert [row["id"] for row in page] == [2]


def test_text_is_shared_per_distinct_response(dataset):
    table = build_table(dataset, dataset.base_colors, False, None, None)
    text = table.text_column()
    assert list(text) == ["OK", "FAIL", "OK", "OK"]
    assert text[0] is text[2] is text[3]
    assert list(per_response(table.responses, len)) == [2, 4, 2, 2]


def test_text_filters_with_repeated_and_null_values(dataset):
    table = build_table(dataset, dataset.base_colors, False, None, None)
    page, count = rows(table, filters={"response": {"filterType": "text", "type": "notContains", "filter": "ok"}})
    assert [row["id"] for row in page] == [2]
    page, count = rows(table, filters={"sometimes": {"filterType": "number", "type": "blank"}})
    assert [row["id"] for row in page] == [1]
    page, count = rows(table, filters={"response": {"filterType": "text", "type": "blank"}})
    assert count == 0


def test_response_filters_work_a_chunk_at_a_time(dataset, monkeypatch):
    monkeypatch.setattr(table_module, "_FILTER_CHUNK", 1)
    table = build_table(dataset, dataset.base_colors, False, None, None)
    page, count = rows(table, filters={"response": {"filterType": "text", "type": "equals", "filter": "ok"}})
    assert [row["id"] for row in page] == [1, 3, 4]
    page, count = rows(table, filters={"hex(response)": {"filterType": "text", "type": "endsWith", "filter": "4c"}})
    assert [row["id"] for row in page] == [2]


def test_filtering_squeezed_min_max(dataset):
    table = build_table(dataset, dataset.base_colors, True, None, None)
    page, count = rows(table, filters={"max(glitch_delay)": {"filterType": "number", "type": "greaterThanOrEqual", "filter": 40}})
    assert [row["response"] for row in page] == ["OK"]


#
# grid_filter_mask
#

VALUES = pd.Series([1, 5, 10, None])
TEXT = pd.Series(["Apple", "banana", "", None])


@pytest.mark.parametrize("kind, value, expected", [
    ("equals", 5, [False, True, False, False]),
    ("notEqual", 5, [True, False, True, True]),
    ("lessThan", 5, [True, False, False, False]),
    ("lessThanOrEqual", 5, [True, True, False, False]),
    ("greaterThan", 5, [False, False, True, False]),
    ("greaterThanOrEqual", 5, [False, True, True, False]),
])
def test_number_filters(kind, value, expected):
    assert list(grid_filter_mask(VALUES, {"filterType": "number", "type": kind, "filter": value})) == expected


def test_number_in_range():
    model = {"filterType": "number", "type": "inRange", "filter": 2, "filterTo": 10}
    assert list(grid_filter_mask(VALUES, model)) == [False, True, True, False]


@pytest.mark.parametrize("kind, value, expected", [
    ("contains", "AN", [False, True, False, False]),
    ("notContains", "an", [True, False, True, True]),
    ("equals", "apple", [True, False, False, False]),
    ("notEqual", "apple", [False, True, True, True]),
    ("startsWith", "ba", [False, True, False, False]),
    ("endsWith", "LE", [True, False, False, False]),
])
def test_text_filters(kind, value, expected):
    assert list(grid_filter_mask(TEXT, {"filterType": "text", "type": kind, "filter": value})) == expected


def test_blank_filters():
    assert list(grid_filter_mask(TEXT, {"filterType": "text", "type": "blank"})) == [False, False, True, True]
    assert list(grid_filter_mask(TEXT, {"filterType": "text", "type": "notBlank"})) == [True, True, False, False]
    assert list(grid_filter_mask(VALUES, {"filterType": "number", "type": "blank"})) == [False, False, False, True]


def test_combined_filters():
    low = {"filterType": "number", "type": "lessThan", "filter": 2}
    high = {"filterType": "number", "type": "greaterThan", "filter": 8}
    either = {"filterType": "number", "operator": "OR", "conditions": [low, high]}
    assert list(grid_filter_mask(VALUES, either)) == [True, False, True, False]
    both = {"filterType": "number", "operator": "AND", "conditions": [low, high]}
    assert list(grid_filter_mask(VALUES, both)) == [False, False, False, False]
    # older AG Grid versions
    legacy = {"filterType": "number", "operator": "OR", "condition1": low, "condition2": high}
    assert list(grid_filter_mask(VALUES, legacy)) == [True, False, True, False]


def test_unknown_filter_keeps_everything():
    assert list(grid_filter_mask(VALUES, {"filterType": "set", "type": "whatever"})) == [True] * 4


def test_export(dataset):
    table = build_table(dataset, dataset.base_colors, False, None, None)
    frame, count = table.export([{"colId": "glitch_delay", "sort": "desc"}],
                                {"response": {"filterType": "text", "type": "equals", "filter": "ok"}},
                                ["id", "response", "hex(response)", "color"], 2)
    assert count == 3
    assert list(frame.columns) == ["id", "color", "response", "hex(response)"]
    assert frame.to_dict("records") == [{"id": 4, "color": "Y", "response": "OK", "hex(response)": "4f 4b"},
                                        {"id": 3, "color": "G", "response": "OK", "hex(response)": "4f 4b"}]


def test_floats_round_trip(dataset):
    table = build_table(dataset, dataset.base_colors, False, None, None)
    assert not any(isinstance(v, float) and math.isnan(v) for v in rows(table)[0][0].values() if v is not None)
