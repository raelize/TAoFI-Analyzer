"""The callbacks, called through Dash's HTTP endpoint like the browser does."""
import os
import sqlite3

import pytest

from analyzer import database
from analyzer.callbacks import column_defs
from analyzer.config import COLORS
from analyzer.table import Table
from tests.conftest import EXAMPLE_ARGV, EXAMPLE_DATABASE, EXAMPLE_DIRECTORY, EXAMPLE_EXPERIMENTS

GRID = '{"index":["MATCH"],"type":"data-grid"}'
GRIDS = '{"index":["ALL"],"type":"data-grid"}'


@pytest.fixture
def client(dash_client):
    return dash_client("127.0.0.1")


@pytest.fixture
def remote(dash_client):
    return dash_client("10.1.2.3")


def select(client, store, database=EXAMPLE_DATABASE, directory=None, trigger="update-button.n_clicks", query="",
           graph="scatter", fixgreen=True, x="delay", y="length", z=None, recolor=None):
    """update_store(): what happens when the user selects a database and clicks Update."""
    values = {
        "update-button.n_clicks": 1, "database-dropdown.value": database, "x-dropdown.value": x, "y-dropdown.value": y,
        "z-dropdown.value": z,
        "refresh-timer.n_intervals": None, "switch-fixgreen.value": fixgreen, "graph-dropdown.value": graph,
        "directory-input.value": directory or store["directory"], "query-input.value": query,
        "config-store.data": store, "jitter-input.value": 0,
    }
    for color in COLORS:
        values[f"recolor-{color}.value"] = (recolor or {}).get(color)
        values[f"recolor-{color}-label.value"] = None
    return client.call("config-store.data", values, trigger)


@pytest.fixture
def store(client):
    code, response = select(client, client.layout_store())
    assert code == 200
    return response["config-store"]["data"]


def graph(client, store, hover=True):
    return client.call("graph.figure", {"graph-store.data": store, "switch-hover.value": hover}, "graph-store.data")


def data(client, store, squeeze=False, hidden=(), wraptext=False, selection=None, trigger="tabs.value"):
    values = {
        "switch-squeezedata.value": squeeze, "switch-wraptext.value": wraptext, "response_s.value": None,
        "response_e.value": None, "tabs.value": "tab-data", "config-store.data": store, "data-hidden-columns.data": list(hidden),
        "selection-store.data": selection, "data-show-all.n_clicks": 1 if trigger == "data-show-all.n_clicks" else 0,
    }
    return client.call("data.children", values, trigger)


def get_rows(client, store, grid_id, squeeze=False, start=0, end=100, sort=(), filters=None):
    request = {"startRow": start, "endRow": end, "sortModel": list(sort), "filterModel": filters or {}}
    values = {"config-store.data": store, "switch-squeezedata.value": squeeze, "response_s.value": None, "response_e.value": None}
    code, response = client.call("getRowsResponse", values, "grid.getRowsRequest",
                                 ids={f"{GRID}.getRowsRequest": (grid_id, request), f"{GRID}.getRowsResponse": grid_id})
    assert code == 200
    return next(iter(response.values()))["getRowsResponse"]


def query(client, store, select_columns="count(*)", where=""):
    values = {"query-button.n_clicks": 1, "select-input.value": select_columns, "where-input.value": where, "config-store.data": store}
    code, response = client.call("query-output.children", values, "query-button.n_clicks")
    assert code == 200
    return response["query-output"]["children"]


#
# page and tabs
#


@pytest.mark.parametrize("tab", ["tab-graph", "tab-data", "tab-information", "tab-database", "tab-guide"])
def test_tabs_render(client, store, tab):
    code, response = client.call("tabs-content.children", {"tabs.value": tab, "config-store.data": store}, "tabs.value")
    assert code == 200


def test_graph_tab_shows_the_store(client, store):
    store = dict(store, x="id", y="delay", z="length", jitter=3, graph="3d", fixgreen=False)
    code, response = client.call("tabs-content.children", {"tabs.value": "tab-graph", "config-store.data": store}, "tabs.value")
    values = {}

    def walk(node):
        if isinstance(node, dict):
            props = node.get("props")
            if isinstance(props, dict) and isinstance(props.get("id"), str):
                values[props["id"]] = props.get("value")
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(response)
    assert values["database-dropdown"] == EXAMPLE_DATABASE
    assert (values["x-dropdown"], values["y-dropdown"], values["z-dropdown"], values["jitter-input"]) == ("id", "delay", "length", 3)
    assert (values["graph-dropdown"], values["switch-fixgreen"]) == ("3d", False)


def test_unknown_tab(client, store):
    code, _ = client.call("tabs-content.children", {"tabs.value": "tab-nope", "config-store.data": store}, "tabs.value")
    assert code == 204


#
# selecting a database
#


def test_select_database(store):
    assert store["directory"] == EXAMPLE_DIRECTORY
    assert store["database"] == EXAMPLE_DATABASE
    assert (store["x"], store["y"]) == ("delay", "length")
    assert store["argv"] == EXAMPLE_ARGV


def test_database_list(client, tmp_path):
    code, response = client.call("database-dropdown.options", {"directory-input.value": EXAMPLE_DIRECTORY}, "directory-input.value")
    assert response["database-dropdown"]["options"] == [{"label": f"{EXAMPLE_DATABASE} ({EXAMPLE_EXPERIMENTS})", "value": EXAMPLE_DATABASE}]
    assert response["directory-input"]["invalid"] is False
    code, response = client.call("database-dropdown.options", {"directory-input.value": str(tmp_path / "missing")}, "directory-input.value")
    assert response == {"database-dropdown": {"options": []}, "directory-input": {"invalid": True}, "count-timer": {"disabled": True}}


def test_database_list_while_counting(client, tmp_path, make_database, monkeypatch):
    make_database()
    monkeypatch.setattr("analyzer.callbacks.counting", lambda: True)
    code, response = client.call("database-dropdown.options", {"directory-input.value": str(tmp_path)}, "directory-input.value")
    # checked again every second until all are counted
    assert response["count-timer"]["disabled"] is False


def test_axis_lists(client, store):
    code, response = client.call("x-dropdown.options", {"database-dropdown.value": EXAMPLE_DATABASE, "config-store.data": store}, "database-dropdown.value")
    assert response["x-dropdown"]["options"] == response["y-dropdown"]["options"] == ["id", "delay", "length", "color"]
    for axis in ("z", "x2", "y2"):
        assert response[f"{axis}-dropdown"]["options"] == ["id", "delay", "length", "color"]


@pytest.mark.parametrize("graph_type, shown", [
    ("scatter", ["x", "y"]),
    ("scatter2", ["x", "y", "x2", "y2"]),
    ("3d", ["x", "y", "z"]),
    ("mega", []),
])
def test_only_the_axes_of_the_graph_type_are_shown(client, graph_type, shown):
    code, response = client.call("x-dropdown.style", {"graph-dropdown.value": graph_type}, "graph-dropdown.value")
    visible = [axis for axis in ("x", "y", "z", "x2", "y2") if response[f"{axis}-dropdown"]["style"] != {"display": "none"}]
    assert visible == shown


def test_switch_directory(client, store, tmp_path, make_database):
    make_database("other.sqlite")
    code, response = select(client, store, database=EXAMPLE_DATABASE, directory=str(tmp_path), trigger="directory-input.value")
    assert code == 200
    switched = response["config-store"]["data"]
    assert switched["directory"] == str(tmp_path)
    # the database is deselected, the axes are kept
    assert switched["database"] is None and response["database-dropdown"]["value"] is None
    assert (response["x-dropdown"]["value"], response["y-dropdown"]["value"]) == ("delay", "length")
    code, response = select(client, switched, database="other.sqlite", directory=str(tmp_path))
    assert response["config-store"]["data"]["database"] == "other.sqlite"


def test_remote_users_cant_switch_directory(remote, tmp_path, make_database):
    make_database("other.sqlite")
    store = remote.layout_store()
    code, _ = select(remote, store, database=None, directory=str(tmp_path), trigger="directory-input.value")
    assert code == 204
    code, response = remote.call("database-dropdown.options", {"directory-input.value": str(tmp_path)}, "directory-input.value")
    assert [o["value"] for o in response["database-dropdown"]["options"]] == [EXAMPLE_DATABASE]
    # a store pointing elsewhere is ignored
    code, _ = select(remote, dict(store, directory=str(tmp_path)), database="other.sqlite", directory=str(tmp_path))
    assert code == 204


def test_auto_refresh_only_when_the_database_changed(client, tmp_path, make_database):
    make_database(rows=[(1, 1, 1, "G", b"x")])
    _, response = select(client, client.layout_store(), database=None, directory=str(tmp_path), trigger="directory-input.value")
    _, response = select(client, response["config-store"]["data"], database="test.sqlite", directory=str(tmp_path))
    store = response["config-store"]["data"]
    code, _ = select(client, store, database="test.sqlite", directory=str(tmp_path), trigger="refresh-timer.n_intervals")
    assert code == 204
    con = sqlite3.connect(tmp_path / "test.sqlite")
    con.execute("INSERT INTO experiments VALUES (2, 2, 2, 'R', x'00')")
    con.commit()
    con.close()
    code, _ = select(client, store, database="test.sqlite", directory=str(tmp_path), trigger="refresh-timer.n_intervals")
    assert code == 200


def test_auto_refresh_keeps_the_applied_text_fields(client, tmp_path, make_database):
    """A query (or recolor regex) that is still being typed isn't applied by an auto refresh."""
    make_database(rows=[(1, 1, 1, "G", b"x")])
    _, response = select(client, client.layout_store(), database=None, directory=str(tmp_path), trigger="directory-input.value")
    _, response = select(client, response["config-store"]["data"], database="test.sqlite", directory=str(tmp_path),
                         query="delay > 0", recolor={"red": "x"})
    store = response["config-store"]["data"]
    append(tmp_path / "test.sqlite")
    _, response = select(client, store, database="test.sqlite", directory=str(tmp_path), trigger="refresh-timer.n_intervals",
                         query="delay > ", recolor={"red": "("})
    refreshed = response["config-store"]["data"]
    assert refreshed["dbkey"] != store["dbkey"]
    assert (refreshed["query"], refreshed["colors"]["red"][0]) == ("delay > 0", "x")


def test_data_of_a_selection(client, store):
    selection = {"directory": store["directory"], "database": store["database"], "query": store["query"], "jitter": store["jitter"],
                 "x": "delay", "y": "length", "box": {"x": [1000, 1100], "y": [1000, 2000]}}
    code, response = data(client, store, selection=selection)
    note = response["data-selection"]["children"]
    assert note.startswith("Showing the ") and "selected in the graph (delay 1000–1100, length 1000–2000)" in note
    assert response["data-selection-bar"]["style"] == {}
    # Show all
    code, response = data(client, store, selection=selection, trigger="data-show-all.n_clicks")
    assert response["data-selection-bar"]["style"] == {"display": "none"}
    # (the second callback with this output: its id has an @)
    code, response = client.call("selection-store.data@", {"data-show-all.n_clicks": 1}, "data-show-all.n_clicks")
    assert response["selection-store"]["data"] is None


def test_download_data(client, store):
    request = {"startRow": 0, "endRow": 100, "sortModel": [{"colId": "id", "sort": "desc"}],
               "filterModel": {"delay": {"filterType": "number", "type": "lessThan", "filter": 1001}}}
    values = {"data-download-button.n_clicks": 1, "data-columns.value": ["id", "delay", "rlen"], "config-store.data": store,
              "switch-squeezedata.value": False, "response_s.value": None, "response_e.value": None, "selection-store.data": None}
    ids = {f"{GRIDS}.getRowsRequest": [({"type": "data-grid", "index": "0-0-None-None-"}, request)]}
    code, response = client.call("data-download.data", values, "data-download-button.n_clicks", ids)
    assert code == 200
    download = response["data-download"]["data"]
    assert download["filename"] == EXAMPLE_DATABASE.replace(".sqlite", ".csv")
    lines = download["content"].splitlines()
    assert lines[0] == "id,delay,rlen"
    ids = [int(line.split(",")[0]) for line in lines[1:]]
    assert ids == sorted(ids, reverse=True) and all(int(line.split(",")[1]) < 1001 for line in lines[1:])
    assert response["data-download-note"]["children"] == f"{len(ids)} rows"


def test_data_tab_auto_refresh(client, tmp_path, make_database):
    make_database(rows=[(1, 1, 1, "G", b"x")])
    store = dict(client.layout_store(), directory=str(tmp_path), database="test.sqlite")

    def refresh(shown):
        values = {"data-refresh-timer.n_intervals": 1, "data-dbkey.data": shown, "config-store.data": store}
        return client.call("data-dbkey.data", values, "data-refresh-timer.n_intervals")

    code, response = refresh("")
    assert code == 200
    shown = response["data-dbkey"]["data"]
    # unchanged: nothing to do
    assert refresh(shown)[0] == 204
    append(tmp_path / "test.sqlite")
    code, response = refresh(shown)
    assert code == 200 and response["data-dbkey"]["data"] != shown


@pytest.mark.parametrize("trigger, applied", [
    ("update-button.n_clicks", True),
    ("query-input.n_submit", True),
    ("recolor-red.n_submit", True),
    ("jitter-input.n_submit", True),
    (None, True),  # the graph tab is shown (e.g. after a reload, with the fields as they were)
    ("x-dropdown.value", False),
    ("graph-dropdown.value", False),
])
def test_text_fields_are_applied_with_update_or_enter(client, store, trigger, applied):
    _, response = select(client, store, trigger=trigger, x="id", query="delay > 1", recolor={"red": "x"})
    new = response["config-store"]["data"]
    assert new["x"] == "id"
    assert (new["query"] == "delay > 1") is applied
    assert (new["colors"]["red"][0] == "x") is applied


def append(path):
    con = sqlite3.connect(path)
    con.execute("INSERT INTO experiments VALUES (2, 2, 2, 'R', x'00')")
    con.commit()
    con.close()


#
# graph
#


def legend(response):
    return [trace["name"] for trace in response["graph"]["figure"]["data"]]


def test_graph(client, store):
    code, response = graph(client, store)
    assert code == 200
    assert legend(response) == ["green ( 3004 / 43.9% )", "yellow ( 3497 / 51.1% )", "magenta ( 298 / 4.4% )", "red ( 44 / 0.6% )"]


@pytest.mark.parametrize("graph_type", ["scatter", "3d", "mega"])
def test_graph_types(client, graph_type):
    _, response = select(client, client.layout_store(), graph=graph_type, z="id")
    code, response = graph(client, response["config-store"]["data"])
    assert code == 200
    assert len(response["graph"]["figure"]["data"]) == 4


def test_3d_graph(client):
    _, response = select(client, client.layout_store(), graph="3d", z="id", recolor={"red": "^"})
    store = response["config-store"]["data"]
    assert (store["z"], response["z-dropdown"]["value"]) == ("id", "id")
    code, response = graph(client, store)
    assert code == 200
    figure = response["graph"]["figure"]
    assert {trace["type"] for trace in figure["data"]} == {"scatter3d"}
    assert legend(response)[-1] == "^ ( 3839 / 56.1% )"
    assert figure["layout"]["scene"]["zaxis"]["title"]["text"] == "id"


@pytest.mark.parametrize("graph_type, axes, message", [
    ("scatter", {"x": None}, "Select the x-axis to show the scatter graph"),
    ("3d", {}, "Select the z-axis to show the 3d graph"),
    ("3d", {"z": "no_such_column"}, f"{EXAMPLE_DATABASE} has no no_such_column column"),
    ("scatter", {"y": "no_such_column"}, f"{EXAMPLE_DATABASE} has no no_such_column column"),
    ("scatter", {"x": "a", "y": "b"}, f"{EXAMPLE_DATABASE} has no a, b columns"),
])
def test_graph_without_its_axes(client, store, graph_type, axes, message):
    code, response = graph(client, dict(store, graph=graph_type, **axes))
    assert code == 200
    assert graph_message(response) == message


def test_scatter2_graph(client, store):
    code, response = graph(client, dict(store, graph="scatter2", x2="id", y2="delay", colors={"red": ["^", "all"]}))
    assert code == 200
    figure = response["graph"]["figure"]
    traces = figure["data"]
    # the same colors in both plots, in the legend once
    assert [(t["xaxis"], t["yaxis"]) for t in traces] == [("x", "y")] * 2 + [("x2", "y2")] * 2
    assert [t["showlegend"] for t in traces] == [True, True, False, False]
    assert legend(response) == ["green ( 3004 / 43.9% )", "all ( 3839 / 56.1% )"] * 2
    assert [figure["layout"][a]["title"]["text"] for a in ("xaxis", "yaxis", "xaxis2", "yaxis2")] == ["delay", "length", "id", "delay"]
    code, response = graph(client, dict(store, graph="scatter2"))
    assert graph_message(response) == "Select the x-axis (right) and y-axis (right) to show the scatter2 graph"


def test_mega_graph(client, store):
    code, response = graph(client, dict(store, graph="mega", x=None, y=None, colors={"red": ["^", "all"]}))
    assert code == 200
    traces = response["graph"]["figure"]["data"]
    assert {trace["type"] for trace in traces} == {"splom"}
    assert [d["label"] for d in traces[0]["dimensions"]] == ["delay", "length"]
    assert legend(response)[-1] == "all ( 3839 / 56.1% )"


def test_mega_graph_needs_two_parameters(client, tmp_path, make_database):
    make_database("one.sqlite", columns="id integer, delay integer, color text, response blob", rows=[(1, 10, "G", b"x")])
    store = dict(client.layout_store(), directory=str(tmp_path), database="one.sqlite", graph="mega")
    code, response = graph(client, store)
    assert graph_message(response) == "The mega graph needs at least two number columns besides id (delay)"


def test_recolored_graph(client):
    _, response = select(client, client.layout_store(), recolor={"red": "^"})
    code, response = graph(client, response["config-store"]["data"])
    # everything except the green experiments is red
    assert legend(response)[0].endswith("( 3004 / 43.9% )")
    assert legend(response)[-1] == "^ ( 3839 / 56.1% )"


def test_graph_keeps_its_zoom(client, store):
    def revision(store):
        return graph(client, store)[1]["graph"]["figure"]["layout"]["uirevision"]

    assert revision(store) == revision(dict(store, query="delay > 1", jitter=5, dbkey="new"))
    assert revision(store) != revision(dict(store, y="id"))
    assert revision(store) != revision(dict(store, graph="mega"))


def click(client, store, curve, point):
    values = {"graph.clickData": {"points": [{"curveNumber": curve, "pointNumber": point}]}, "config-store.data": store}
    return client.call("experiment.children", values, "graph.clickData")


def text(node):
    """All text in a (Dash JSON) component tree."""
    if isinstance(node, dict):
        return text(node.get("props", {}).get("children")) if "props" in node else " ".join(text(v) for v in node.values())
    if isinstance(node, list):
        return " ".join(text(child) for child in node)
    return "" if node is None else str(node)


def test_clicked_experiment(client, tmp_path, make_database):
    make_database(rows=[(1, 10, 3, "G", b"ok"), (2, 20, 3, "R", b"crash\x00"), (3, 30, 3, "R", b"crash\x00"),
                        (4, 40, 3, "Y", b"ok")])
    # recolored: G (ids 1, 2, 3) and Y (id 4); the traces in COLOR_ORDER
    store = dict(client.layout_store(), directory=str(tmp_path), database="test.sqlite", x="delay", y="length",
                 colors={"green": ["crash", None]}, fixgreen=False)
    code, response = click(client, store, 0, 1)
    assert code == 200
    shown = text(response["experiment"]["children"])
    assert "id 2" in shown and "delay 20" in shown
    assert "R (shown as G)" in shown
    assert "6 bytes, the same for 2 of 4 experiments" in shown
    assert "63 72 61 73 68 00" in shown
    assert "id 4" in text(click(client, store, 1, 0)[1]["experiment"]["children"])
    # scatter2: the same traces in the second plot
    scatter2 = dict(store, graph="scatter2", x2="length", y2="id")
    assert "id 4" in text(click(client, scatter2, 3, 0)[1]["experiment"]["children"])
    assert "id 2" in text(click(client, scatter2, 2, 1)[1]["experiment"]["children"])
    # e.g. a graph of before the database was loaded again
    for curve, point in [(0, 3), (2, 0)]:
        code, response = click(client, store, curve, point)
        assert text(response["experiment"]["children"]) == "The database changed: click the point again."


def test_graph_without_hover(client, store):
    code, response = graph(client, store, hover=False)
    assert response["graph"]["figure"]["layout"]["hovermode"] is False


def test_graph_of_a_non_analyzer_database(client, store, tmp_path, make_database):
    make_database("no-response.sqlite", columns="id integer, delay integer, length integer, color text", rows=[(1, 1, 1, "G")])
    code, response = graph(client, dict(store, directory=str(tmp_path), database="no-response.sqlite"))
    assert code == 200
    assert graph_message(response) == "no-response.sqlite has no response column"


def graph_message(response):
    return response["graph"]["figure"]["layout"]["annotations"][0]["text"]


def test_a_bad_query_is_shown(client, store):
    store = dict(store, query="no_such_column > 1")
    code, response = graph(client, store)
    assert code == 200
    assert graph_message(response) == f"{EXAMPLE_DATABASE}: no such column: no_such_column"
    code, response = data(client, store)
    assert code == 200
    assert response["data"]["children"]["props"]["children"] == f"{EXAMPLE_DATABASE}: no such column: no_such_column"
    assert response["data-columns"]["options"] == []


def test_not_enough_memory_is_shown(client, store, monkeypatch):
    monkeypatch.setattr(database, "available_memory", lambda: 100 * 2**20)
    code, response = graph(client, store)
    assert code == 200
    assert graph_message(response) == (f"{EXAMPLE_DATABASE}: not enough memory: stopped after 6843 experiments "
                                       "(100 MB left); use a query to load fewer experiments")


#
# data tab
#


def test_data(client, store):
    code, response = data(client, store, hidden=["hex(response)"])
    assert code == 200
    grid = response["data"]["children"]["props"]
    assert response["data-columns"]["options"] == ["id", "delay", "length", "color", "rlen", "response", "hex(response)"]
    assert response["data-columns"]["value"] == ["id", "delay", "length", "color", "rlen", "response"]
    assert [d["field"] for d in grid["columnDefs"] if d["hide"]] == ["hex(response)"]
    rows = get_rows(client, store, grid["id"], start=0, end=5)
    assert rows["rowCount"] == EXAMPLE_EXPERIMENTS
    assert [row["id"] for row in rows["rowData"]] == [0, 1, 2, 3, 4]


def test_data_squeezed(client, store):
    code, response = data(client, store, squeeze=True)
    grid = response["data"]["children"]["props"]
    assert response["data-columns"]["options"][:3] == ["amount", "id >", "id <"]
    rows = get_rows(client, store, grid["id"], squeeze=True, filters={"delay <": {"filterType": "number", "type": "greaterThan", "filter": 1990}},
                    sort=[{"colId": "amount", "sort": "desc"}])
    amounts = [row["amount"] for row in rows["rowData"]]
    assert amounts == sorted(amounts, reverse=True)
    assert all(row["delay <"] > 1990 for row in rows["rowData"])


def test_data_is_colored_like_the_graph(client):
    _, response = select(client, client.layout_store(), recolor={"red": "^"})
    store = response["config-store"]["data"]
    _, response = data(client, store)
    rows = get_rows(client, store, response["data"]["children"]["props"]["id"], end=1000)
    assert {row["color"] for row in rows["rowData"]} == {"G", "R"}


def test_rows_per_request_are_capped(client, store):
    _, response = data(client, store)
    rows = get_rows(client, store, response["data"]["children"]["props"]["id"], end=10**9)
    assert len(rows["rowData"]) == 1000


def test_empty_data(client):
    _, response = select(client, client.layout_store(), query="delay < 0")
    store = response["config-store"]["data"]
    for squeeze in (False, True):
        code, response = data(client, store, squeeze=squeeze)
        assert code == 200
        assert get_rows(client, store, response["data"]["children"]["props"]["id"], squeeze=squeeze)["rowCount"] == 0


def test_show_and_hide_columns(client, store):
    _, response = data(client, store, hidden=["hex(response)", "only-when-squeezed >"])
    grid = response["data"]["children"]["props"]
    shown = [c for c in response["data-columns"]["value"] if c not in ("delay", "rlen")] + ["hex(response)"]
    values = {"data-columns.value": shown, "data-columns.options": response["data-columns"]["options"],
              "data-hidden-columns.data": ["hex(response)", "only-when-squeezed >"]}
    code, response = client.call("data-hidden-columns.data", values, "data-columns.value", ids={
        f"{GRIDS}.columnDefs": [(grid["id"], grid["columnDefs"])],
    })
    assert code == 200
    # hidden columns of the other (squeezed) table are kept
    assert response["data-hidden-columns"]["data"] == ["delay", "only-when-squeezed >", "rlen"]
    defs = next(v for k, v in response.items() if "data-grid" in k)["columnDefs"]
    assert [d["field"] for d in defs if d["hide"]] == ["delay", "rlen"]


def test_column_defs():
    import pandas as pd

    table = Table(pd.DataFrame({"id": [1], "note": ["a"], "color": ["G"]}), [b"x"], squeezed=False, numeric={"id"})
    defs = {d["field"]: d for d in column_defs(table, wraptext=False, hidden={"note"})}
    assert defs["id"]["filter"] == defs["rlen"]["filter"] == "agNumberColumnFilter"
    assert defs["note"]["filter"] == defs["color"]["filter"] == defs["response"]["filter"] == "agTextColumnFilter"
    assert defs["note"]["hide"] and not defs["id"]["hide"]
    assert defs["id"]["pinned"] == defs["color"]["pinned"] == "left"
    assert defs["hex(response)"]["cellStyle"] == {"font-family": "monospace"}
    wrapped = {d["field"]: d for d in column_defs(table, wraptext=True, hidden=set())}
    assert wrapped["response"]["wrapText"] and wrapped["hex(response)"]["wrapText"]


#
# database tab
#


def test_query(client, store):
    assert query(client, store).split() == ["count(*)", str(EXAMPLE_EXPERIMENTS)]
    con = sqlite3.connect(os.path.join(EXAMPLE_DIRECTORY, EXAMPLE_DATABASE))
    expected = [str(i) for (i,) in con.execute("SELECT id FROM experiments WHERE instr(response, x'ff') AND id < 50")]
    con.close()
    assert len(expected) > 1
    assert query(client, store, "id", "match_hex(response, 'ff') AND id < 50").split() == ["id"] + expected


def test_query_errors(client, store):
    assert query(client, store, "nope").startswith("ERROR:")
    assert query(client, dict(store, database="../../etc/passwd")) == "ERROR: select a database on the Graph tab first"
    assert query(client, dict(store, database=None)) == "ERROR: select a database on the Graph tab first"


def test_slow_query(client, store, monkeypatch):
    monkeypatch.setattr(database, "QUERY_TIMEOUT", 0.5)
    where = "(WITH RECURSIVE r(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM r WHERE i < 20000000) SELECT count(*) FROM r) > 0"
    assert query(client, store, where=where) == "ERROR: the query took longer than 0.5 s"


def test_no_files_are_created(client, store):
    before = sorted(os.listdir(EXAMPLE_DIRECTORY))
    query(client, dict(store, database=None))
    query(client, dict(store, database="new.sqlite"))
    assert sorted(os.listdir(EXAMPLE_DIRECTORY)) == before


#
# information tab
#


def test_information(client, store):
    _, response = client.call("argv.children", {"config-store.data": store}, "config-store.data")
    assert response["argv"]["children"] == EXAMPLE_ARGV
    _, response = client.call("printstore.children", {"config-store.data": store}, "config-store.data")
    assert f"* database:{EXAMPLE_DATABASE}" in response["printstore"]["children"]


def test_zoomed_points(client, store):
    def points(zoom):
        _, response = client.call("points.children", {"zoom-store.data": zoom, "config-store.data": store}, "zoom-store.data")
        return response["points"]["children"]

    assert points(None) == "Zoom into the graph to see the selected range here."
    assert points({"xaxis": [1100, 1500.5]}) == "* delay: 1100 – 1500.5\n* length: (not zoomed)\n"
    assert points({"xaxis": [1, 2], "yaxis": [3, 4]}) == "* delay: 1 – 2\n* length: 3 – 4\n"


#
# directory browser
#


def browse(client, trigger, directory=None, browsed=None, entries=()):
    values = {"directory-browse.n_clicks": 1, "browse-select.n_clicks": 1, "browse-cancel.n_clicks": 1,
              "directory-input.value": directory, "browse-directory.data": browsed}
    entry_id = '{"index":["ALL"],"type":"browse-entry"}.n_clicks'
    return client.call("directory-modal.is_open", values, trigger, ids={entry_id: list(entries)})


def test_directory_browser(client, tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "a.sqlite").touch()

    code, response = browse(client, "directory-browse.n_clicks", directory=str(tmp_path))
    assert response == {"directory-modal": {"is_open": True}, "browse-directory": {"data": str(tmp_path)}}

    code, response = client.call("browse-list.children", {"browse-directory.data": str(tmp_path)}, "browse-directory.data")
    entries = [item["props"]["id"]["index"] for item in response["browse-list"]["children"]]
    assert entries == [str(tmp_path.parent), str(tmp_path / "sub")]

    # clicking an entry walks into it; new (unclicked) entries don't
    sub = {"index": str(tmp_path / "sub"), "type": "browse-entry"}
    code, response = browse(client, '{"index":"%s","type":"browse-entry"}.n_clicks' % (tmp_path / "sub"), entries=[(sub, 1)])
    assert response == {"browse-directory": {"data": str(tmp_path / "sub")}}

    code, response = browse(client, "browse-select.n_clicks", browsed=str(tmp_path / "sub"))
    assert response == {"directory-modal": {"is_open": False}, "directory-input": {"value": str(tmp_path / "sub")}}

    code, response = browse(client, "browse-cancel.n_clicks")
    assert response == {"directory-modal": {"is_open": False}}


def test_directory_browser_is_local_only(remote, tmp_path):
    code, _ = browse(remote, "directory-browse.n_clicks", directory=str(tmp_path))
    assert code == 204
    code, _ = remote.call("browse-list.children", {"browse-directory.data": str(tmp_path)}, "browse-directory.data")
    assert code == 204
