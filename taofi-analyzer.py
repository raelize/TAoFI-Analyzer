#!/usr/bin/env python3
import argparse
import os
import re
import sqlite3
import sys
import threading
from collections import OrderedDict
from contextlib import closing
from dataclasses import asdict, dataclass, field
from operator import itemgetter
from os import listdir
from pathlib import Path
from typing import Dict
import random

import dash_bootstrap_components as dbc
import numpy as np
import pandas as pd
import plotly.express as px
from dash import MATCH, Dash, Input, Output, State, dcc, html
from dash import callback_context as ctx
from dash import no_update
from dash.exceptions import PreventUpdate
from dash_ag_grid import AgGrid
from dataclasses_json import dataclass_json

try:
    import tomllib  # Python 3.11+
except ImportError:
    import tomli as tomllib  # Backport for <3.11


#
# PROJECT METADATA
#


def get_project_metadata():
    """Load project metadata from pyproject.toml."""
    default_metadata = {
        "version": "2.1.0",
        "description": "A Dash-based web application for analyzing glitch experiment data",
    }

    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        pyproject_path = Path(os.path.join(current_dir, "pyproject.toml"))
        if pyproject_path.exists():
            with open(pyproject_path, "rb") as f:
                data = tomllib.load(f)
                project = data.get("project", {})
                return {
                    "version": project.get("version", default_metadata["version"]),
                    "description": project.get(
                        "description", default_metadata["description"]
                    ),
                }
        else:
            print(
                f"WARNING: pyproject.toml not found at {pyproject_path}. Using default metadata.",
                file=sys.stderr,
            )
        return default_metadata
    except Exception:
        return default_metadata


PROJECT_METADATA = get_project_metadata()

#
# Dataclasses
#


@dataclass_json
@dataclass
class AnalyzerConfig:
    serverip: str = "127.0.0.1"
    serverport: int = 8080
    directory: str = ""
    database: str = ""
    y: str = ""
    x: str = ""
    jitter: int = 0
    argv: str = ""
    database: str = ""
    query: str = ""
    colors: Dict[str, str] = field(default_factory=dict)


#
# Globals
#

_config = AnalyzerConfig()

# Loaded databases and derived tables are cached per worker process (see Dataset).
_DATASET_CACHE = OrderedDict()
_DATASET_CACHE_SIZE = 3
_TABLE_CACHE = OrderedDict()
_TABLE_CACHE_SIZE = 3
_COUNT_CACHE = {}
_CACHE_LOCK = threading.Lock()
_PAGE_SIZE = 100

_COLORS = ["green", "yellow", "magenta", "orange", "cyan", "blue", "black", "red"]

_COLOR_CONFIG = {
    "P": ("pink", "black", "timeout"),
    "G": ("green", "white", "green"),
    "Y": ("yellow", "black", "yellow"),
    "M": ("magenta", "white", "magenta"),
    "O": ("orange", "white", "orange"),
    "C": ("cyan", "white", "cyan"),
    "B": ("blue", "white", "blue"),
    "Z": ("black", "white", "black"),
    "R": ("red", "white", "red"),
}

for color in _COLORS:
    _config.colors[color] = [None, None]

#
# Functions
#

def file_key(path):
    """Identity of a database file: changes whenever the file is (re)written."""
    st = os.stat(path)
    return (os.path.abspath(path), st.st_mtime_ns, st.st_size)


def get_number_of_experiments(directory, database):
    database_path = os.path.join(directory, database)

    try:
        key = file_key(database_path)
        if key in _COUNT_CACHE:
            return _COUNT_CACHE[key]
        with closing(sqlite3.connect(database_path)) as connection:
            with closing(connection.cursor()) as cursor:
                cursor.execute("SELECT COUNT(*) FROM experiments")
                count = cursor.fetchone()[0]
        _COUNT_CACHE[key] = count
        return count
    except Exception as e:
        print("ERROR (get_number_of_experiments): %s" % (e))


def get_databases(directory):
    # get all databases in directory
    databases = []
    for file in listdir(directory):
        if re.search("^.*\\.sqlite$", file):
            databases.append(file)
    databases.sort(reverse=True)

    # transform to options
    databases_options = []
    for index in range(len(databases)):
        label = "%s (%d)" % (
            databases[index],
            get_number_of_experiments(directory, databases[index]),
        )
        databases_options.append({"label": label, "value": databases[index]})

    return databases_options


def get_argv(directory, database):
    database_path = os.path.join(directory, database)

    try:
        with closing(sqlite3.connect(database_path)) as connection:
            with closing(connection.cursor()) as cursor:
                cursor.execute("SELECT argv FROM metadata")
                argvstr = cursor.fetchone()[0]
                return argvstr
    except Exception as e:
        print("ERROR (get_argv): %s" % (e))


def get_parameters(directory, database):
    database_path = os.path.join(directory, database)

    try:
        with closing(sqlite3.connect(database_path)) as connection:
            with closing(connection.cursor()) as cursor:
                cursor.execute("SELECT * FROM experiments")
                parameters = list(next(zip(*cursor.description)))
                parameters.remove("response")
                return parameters
    except Exception as e:
        print("ERROR (get_parameters): %s" % (e))


# new function for sqlite3 query
def match_string(response, token):
    if token.encode(errors="strict") in response:
        return True
    else:
        return False


# new function for sqlite3 query
def match_hex(response, token):
    if bytes.fromhex(token) in response:
        return True
    else:
        return False



class VariableNames:
    def __init__(self, record):
        self.id = self.get_variable_name(record, ["id"])
        self.color = self.get_variable_name(record, ["color"])
        self.normal = self.get_variable_name(record, ["normal", "normal_voltage"])
        self.delay = self.get_variable_name(record, ["delay", "glitch_delay"])
        self.length = self.get_variable_name(record, ["length", "glitch_length"])
        self.voltage = self.get_variable_name(record, ["voltage", "glitch_voltage"])
        self.power = self.get_variable_name(record, ["power", "glitch_power"])
        self.response = self.get_variable_name(record, ["response"])
        self.reset = self.get_variable_name(record, ["reset"])
        self.x = self.get_variable_name(record, ["x"])
        self.y = self.get_variable_name(record, ["y"])
        self.z = self.get_variable_name(record, ["z"])

    def get_variable_name(self, record, names):
        for name in names:
            if name in record:
                return name
        else:
            return None


def glitch_parameter_present(record, parameter):
    if parameter in record and record[parameter] not in [None]:
        return True
    else:
        return False


def slice_response(response, s, e):
    # slice the response
    if s is None and e is None:
        response = response
    elif s is not None and e is None:
        response = response[s:]
    elif s is None and e is not None:
        response = response[:e]
    elif s is not None and e is not None:
        response = response[s:e]

    return response


class Dataset:
    """The experiments of one database (optionally filtered by the query), as a DataFrame.

    Responses are factorized once so that recolor regexes only run on the
    unique responses; the resulting masks are cached per regex.
    """

    def __init__(self, key, df):
        self.key = key
        self.df = df
        self.names = VariableNames(df.columns)
        responses = df[self.names.response]
        self.resp_codes, self.resp_uniques = pd.factorize(responses, use_na_sentinel=False)
        self.resp_uniques = np.asarray(self.resp_uniques, dtype=object)
        self.base_colors = df[self.names.color].to_numpy(dtype=object)
        # colors as shown in the last rendered graph (recolor applied)
        self.colors = self.base_colors
        self.color_version = 0
        self._mask_cache = {}
        self._lock = threading.Lock()

    def match_mask(self, regex, flags=0):
        """Boolean mask over all experiments whose response matches ``regex``."""
        cache_key = (regex, flags)
        with self._lock:
            mask = self._mask_cache.get(cache_key)
        if mask is None:
            pattern = re.compile(regex.encode(), flags)
            unique_mask = np.fromiter(
                (pattern.search(r) is not None for r in self.resp_uniques),
                dtype=bool,
                count=len(self.resp_uniques),
            )
            mask = unique_mask[self.resp_codes]
            with self._lock:
                self._mask_cache[cache_key] = mask
        return mask

    def recolor(self, regexes, color_codes, fixgreen, flags=0):
        """Apply the recolor fields in order (same semantics as the old per-record loop)."""
        colors = self.base_colors.copy()
        for regex, code in zip(regexes, color_codes):
            if regex in [None, ""]:
                continue
            try:
                mask = self.match_mask(regex, flags)
            except re.error as e:
                print(f"WARNING: invalid recolor regex {regex!r}: {e}")
                continue
            if fixgreen:
                mask = mask & (colors != "G")
            colors[mask] = code
        with self._lock:
            self.colors = colors
            self.color_version += 1
        return colors


def load_dataframe(database_path, query):
    con = sqlite3.connect(database_path)

    # add some functions to sqlite
    con.create_function("match_string", 2, match_string)
    con.create_function("match_hex", 2, match_hex)

    # perform the query based on the query extension
    if query == "":
        sql = "SELECT * FROM experiments;"
    else:
        sql = f"SELECT * FROM experiments WHERE {query};"

    try:
        cursor = con.execute(sql)
        columns = [d[0] for d in cursor.description]
        rows = cursor.fetchall()
    finally:
        con.close()

    df = pd.DataFrame(rows, columns=columns)
    if "response" in df.columns:
        df["response"] = df["response"].map(lambda r: b"" if r is None else r)
    return df


def get_dataset(config):
    """Cached, loaded database for the given directory/database/query."""
    if not database_exists(config.directory, config.database):
        raise PreventUpdate

    database_path = os.path.join(config.directory, config.database)
    key = file_key(database_path) + (config.query or "",)

    with _CACHE_LOCK:
        dataset = _DATASET_CACHE.get(key)
        if dataset is not None:
            _DATASET_CACHE.move_to_end(key)
            return dataset

    try:
        df = load_dataframe(database_path, config.query or "")
    except Exception as e:
        print("ERROR (get_dataset): %s" % (e))
        raise PreventUpdate

    dataset = Dataset(key, df)
    with _CACHE_LOCK:
        _DATASET_CACHE[key] = dataset
        while len(_DATASET_CACHE) > _DATASET_CACHE_SIZE:
            _DATASET_CACHE.popitem(last=False)
    return dataset


class Table:
    """Rows for the data tab (squeezed or not), served page by page to the grid.

    ``df`` holds the plain columns, ``responses`` the (sliced) raw responses;
    the decoded/hex representation is only produced for the rows being shown,
    or for all rows when the grid sorts/filters on them.
    """

    PINNED = ["id", "color", "normal", "delay", "length", "power", "rlen", "x", "y", "z"]

    def __init__(self, df, responses, squeezed):
        self.df = df
        self.responses = responses
        self.squeezed = squeezed
        self._text = None
        self._hex = None
        self._selection = None  # (sort_model, filter_model) -> row indices
        self._lock = threading.Lock()

    @property
    def columns(self):
        plain = list(self.df.columns)
        if self.squeezed:
            return plain + ["response", "rlen", "hex(response)"]
        return plain + ["rlen", "response", "hex(response)"]

    @staticmethod
    def decode(response):
        return response.decode("utf-8", errors="replace")

    @staticmethod
    def hexify(response):
        return response.hex(" ")

    def text_column(self):
        if self._text is None:
            self._text = np.array([self.decode(r) for r in self.responses], dtype=object)
        return self._text

    def hex_column(self):
        if self._hex is None:
            self._hex = np.array([self.hexify(r) for r in self.responses], dtype=object)
        return self._hex

    def column(self, name):
        if name == "response":
            return pd.Series(self.text_column())
        if name == "hex(response)":
            return pd.Series(self.hex_column())
        if name == "rlen":
            return pd.Series([len(r) for r in self.responses])
        return self.df[name].reset_index(drop=True)

    def select(self, sort_model, filter_model):
        """Row indices after applying the grid's filter and sort model (cached)."""
        cache_key = (repr(sort_model), repr(filter_model))
        with self._lock:
            if self._selection is not None and self._selection[0] == cache_key:
                return self._selection[1]

        mask = np.ones(len(self.responses), dtype=bool)
        for name, model in (filter_model or {}).items():
            if name in self.columns:
                mask &= grid_filter_mask(self.column(name), model)
        indices = np.flatnonzero(mask)

        if sort_model:
            frame = pd.DataFrame(
                {s["colId"]: self.column(s["colId"]).to_numpy()[indices] for s in sort_model}
            )
            order = frame.sort_values(
                by=[s["colId"] for s in sort_model],
                ascending=[s["sort"] == "asc" for s in sort_model],
                kind="stable",
            ).index.to_numpy()
            indices = indices[order]

        with self._lock:
            self._selection = (cache_key, indices)
        return indices

    def page(self, start, end, sort_model, filter_model):
        indices = self.select(sort_model, filter_model)
        page_indices = indices[start:end]
        rows = self.df.iloc[page_indices].to_dict("records")
        for row, index in zip(rows, page_indices):
            response = self.responses[index]
            row["rlen"] = len(response)
            row["response"] = self.decode(response)
            row["hex(response)"] = self.hexify(response)
        return rows, int(len(indices))


def grid_filter_mask(values, model):
    """Evaluate an AG Grid text/number filter model against a column."""
    if "conditions" in model or "condition1" in model:
        conditions = model.get("conditions") or [model["condition1"], model["condition2"]]
        masks = [grid_filter_mask(values, c) for c in conditions]
        if model.get("operator", "AND") == "OR":
            return np.logical_or.reduce(masks)
        return np.logical_and.reduce(masks)

    kind = model.get("type", "contains")
    if kind == "blank":
        return values.isna().to_numpy() | (values.astype(str) == "").to_numpy()
    if kind == "notBlank":
        return ~(values.isna().to_numpy() | (values.astype(str) == "").to_numpy())

    if model.get("filterType") == "number":
        a = pd.to_numeric(values, errors="coerce")
        f = model.get("filter")
        t = model.get("filterTo")
        ops = {
            "equals": lambda: a == f,
            "notEqual": lambda: a != f,
            "lessThan": lambda: a < f,
            "lessThanOrEqual": lambda: a <= f,
            "greaterThan": lambda: a > f,
            "greaterThanOrEqual": lambda: a >= f,
            "inRange": lambda: (a >= f) & (a <= t),
        }
    else:
        s = values.astype(str).str.lower()
        f = str(model.get("filter", "")).lower()
        ops = {
            "contains": lambda: s.str.contains(f, regex=False),
            "notContains": lambda: ~s.str.contains(f, regex=False),
            "equals": lambda: s == f,
            "notEqual": lambda: s != f,
            "startsWith": lambda: s.str.startswith(f),
            "endsWith": lambda: s.str.endswith(f),
        }
    if kind not in ops:
        return np.ones(len(values), dtype=bool)
    return ops[kind]().fillna(False).to_numpy(dtype=bool)


def build_table(dataset, squeeze_records, response_s, response_e):
    df = dataset.df
    v = dataset.names

    def present(name):
        return name is not None and name in df.columns and pd.notna(df[name].iloc[0])

    parameters = [
        (label, column)
        for label, column in [
            ("normal", v.normal),
            ("length", v.length),
            ("power", v.power),
            ("voltage", v.voltage),
            ("reset", v.reset),
        ]
        if present(column)
    ]
    has_xyz = present(v.x) and present(v.y) and present(v.z)

    # slice response
    responses = df[v.response].to_numpy(dtype=object)
    if response_s is not None or response_e is not None:
        responses = np.array(
            [slice_response(r, response_s, response_e) for r in responses], dtype=object
        )

    if not squeeze_records:
        out = pd.DataFrame({"id": df[v.id].to_numpy(), "color": dataset.colors, "delay": df[v.delay].to_numpy()})
        for label, column in parameters:
            out[label] = df[column].to_numpy()
        if has_xyz:
            for axis in ("x", "y", "z"):
                out[axis] = df[getattr(v, axis)].to_numpy()
        return Table(out, responses, squeezed=False)

    grouped = pd.DataFrame({"response": responses, "color": dataset.colors, "delay": df[v.delay].to_numpy()})
    aggregations = {
        "amount": ("color", "size"),
        "color": ("color", "first"),
        "Delay >": ("delay", "min"),
        "Delay <": ("delay", "max"),
    }
    for label, column in parameters:
        grouped[label] = df[column].to_numpy()
        aggregations[f"{label.capitalize()} >"] = (label, "min")
        aggregations[f"{label.capitalize()} <"] = (label, "max")

    squeezed = grouped.groupby("response", sort=False).agg(**aggregations)
    squeezed = squeezed.sort_values("amount", ascending=False, kind="stable")
    responses = squeezed.index.to_numpy(dtype=object)
    return Table(squeezed.reset_index(drop=True), responses, squeezed=True)


def get_table(dataset, squeeze_records, response_s, response_e):
    """Cached table for the data tab."""
    key = (dataset.key, dataset.color_version, bool(squeeze_records), response_s, response_e)
    with _CACHE_LOCK:
        table = _TABLE_CACHE.get(key)
        if table is not None:
            _TABLE_CACHE.move_to_end(key)
            return table
    table = build_table(dataset, squeeze_records, response_s, response_e)
    with _CACHE_LOCK:
        _TABLE_CACHE[key] = table
        while len(_TABLE_CACHE) > _TABLE_CACHE_SIZE:
            _TABLE_CACHE.popitem(last=False)
    return table


def give_xy_label(parameter):
    labels = {
        "normal": "(v)",
        "normal_voltage": "(v)",
        "length": "(ns)",
        "glitch_length": "(ns)",
        "delay": "(ns)",
        "glitch_delay": "(ns)",
        "power": "(%)",
        "glitch_power": "(%)",
        "voltage": "(v)",
        "glitch_voltage": "(v)",
    }
    return labels.get(parameter, "")


def database_exists(directory, database):
    if directory is None or database is None:
        return False

    database_path = os.path.join(directory, database)
    if os.path.exists(database_path):
        return True
    else:
        return False


#
# Callbacks
#


def register_callbacks(app):
    # callback for zoomed doints
    @app.callback(
        Output("points", "children"),
        [
            Input("graph", "relayoutData"), 
            Input("graph", "figure")
        ],
        prevent_initial_call=False,
    )
    def zoomed_points(relayoutData, figure):
        if not figure or not relayoutData or "xaxis.range[0]" not in relayoutData:
            raise PreventUpdate

        layout = figure["layout"]
        x_axis = layout["xaxis"]
        y_axis = layout["yaxis"]

        if "xaxis.range[0]" in relayoutData:
            ranges = {
                "x": (relayoutData["xaxis.range[0]"], relayoutData["xaxis.range[1]"]),
                "y": (relayoutData["yaxis.range[0]"], relayoutData["yaxis.range[1]"]),
            }
        else:
            ranges = {"x": tuple(x_axis["range"]), "y": tuple(y_axis["range"])}

        p = f"""
            * {x_axis["title"]["text"]}
                * {ranges["x"][0]}
                * {ranges["x"][1]} 
            * {y_axis["title"]["text"]}
                * {ranges["y"][0]}
             * {ranges["y"][1]}
        """
        return p

    # callback for printing store at the bottom
    @app.callback(Output("printstore", "children"), Input("config-store", "data"))
    def printstore(store):
        p = ""
        for key, value in store.items():
            p += f"* {key}:{value}\n"
        return p

    # callback for updating database list after clickin the button
    @app.callback(
        Output("database-dropdown", "options"),
        Input("update-button", "n_clicks"),
    )
    def update_database_list(nr_of_clicks):
        return get_databases(_config.directory)

    # callback for updating store
    @app.callback(
        Output("config-store", "data"),
        Output("database-dropdown", "value"),
        Output("x-dropdown", "value"),
        Output("y-dropdown", "value"),
        [
            Input("update-button", "n_clicks"),
            Input("database-dropdown", "value"),
            Input("x-dropdown", "value"),
            Input("y-dropdown", "value"),
        ],
        State("query-input", "value"),
        State("config-store", "data"),
        State("jitter-input", "value"),
        [State(f"recolor-{color}", "value") for color in _COLORS]
        + [State(f"recolor-{color}-label", "value") for color in _COLORS],
    )
    # def update_store(nr_of_clicks, contents, query, database, x, y, store, *color_states):
    def update_store(nr_of_clicks, database, x, y, query, store, jitter, *color_states):
        if database is None:
            raise PreventUpdate

        config = AnalyzerConfig(**store)
        
        # check if database exists
        if not database_exists(config.directory, database):
            print("database does not exist")
            raise PreventUpdate

        # remove number of arguments
        database = database.split(" ")[0]

        config.database = database
        config.x = x
        config.y = y
        config.jitter = jitter
        config.query = query
        config.argv = get_argv(config.directory, config.database)

        # Update color in config
        for color, regex, label in zip(_COLORS, color_states[:8], color_states[8:]):
            config.colors[color] = [regex, label]

        return asdict(config), config.database, config.x, config.y

    # callback for printing the argv string at the bottom
    @app.callback(
        Output("argv", "children"),
        Input("config-store", "data"),
        prevent_initial_call=True,
    )
    def update_argv(store):
        config = AnalyzerConfig(**store)
        return config.argv

    # callback for x list
    @app.callback(
        Output("x-dropdown", "options"),
        Input("database-dropdown", "value"),
        State("config-store", "data"),
        prevent_initial_call=True,
    )
    def update_dropdown_x(database, store):
        config = AnalyzerConfig(**store)
        if database_exists(config.directory, database):
            return get_parameters(config.directory, database)
        else:
            raise PreventUpdate

    # callback for y list
    @app.callback(
        Output("y-dropdown", "options"),
        Input("database-dropdown", "value"),
        State("config-store", "data"),
        prevent_initial_call=True,
    )
    def update_dropdown_y(database, store):
        config = AnalyzerConfig(**store)
        if database_exists(config.directory, database):
            return get_parameters(config.directory, database)
        else:
            raise PreventUpdate



    # callback graph; chained from update_store()
    @app.callback(
        Output("graph", "figure"),
        [
            Input("config-store", "data"),
            Input("x-dropdown", "value"),
            Input("y-dropdown", "value"),
            Input("switch-fixgreen", "value"),
            Input('graph-dropdown', 'value')
        ],
        [State(f"recolor-{color}", "value") for color in _COLORS]
        + [State(f"recolor-{color}-label", "value") for color in _COLORS],
        prevent_initial_call=True,
    )
    def update_graph(store, x, y, fixgreen, selected_callback, *color_states):
        from graphs.scatter import scatter_func
        from graphs.scatter2 import scatter2_func
        from graphs.simple import simple_func
        
        CALLBACKS = {
            'scatter': scatter_func,
            'scatter2': scatter2_func,
            'simple': simple_func
        }

        config = AnalyzerConfig(**store)

        if ctx.triggered_id == "config-store":
            # update x and y
            x = config.x
            y = config.y

        # prevent update
        if any(v is None for v in [x, y]):
            raise PreventUpdate

        dataset = get_dataset(config)

        # create graph
        if selected_callback in CALLBACKS:
            callback_func = CALLBACKS[selected_callback]
            result = callback_func(x, y, fixgreen, dataset, config, _COLORS, *color_states)
            return result
        raise PreventUpdate

    # callback data; chained from update_graph()
    @app.callback(
        Output("data", "children"),
        [
            # Input("config-store", "data"),
            # Input("graph", "figure"),
            Input("switch-squeezedata", "value"),
            Input("switch-showhexdata", "value"),
            Input("switch-wraptext", "value"),
            Input("response_s", "value"),
            Input("response_e", "value"),
            Input('tabs', 'value')
        ],
        State("config-store", "data"),
        prevent_initial_call=False,
    )
    def update_data(squeeze, showhex, wraptext, response_s, response_e, tab, store):
        config = AnalyzerConfig(**store)

        if not config.database:
            raise PreventUpdate

        # squeeze data (or not); only the column layout is needed here, the
        # rows are fetched page by page by get_rows()
        table = get_table(get_dataset(config), squeeze, response_s, response_e)
        columns = table.columns

        fields = []
        configs = []

        for column in columns:
            fields.append(column)
            if column in ["id", "color", "normal", "delay", "length", "power", "rlen", "x", "y", "z"]:
                configs.append(
                    {
                        "autoSize": True,
                        "maxWidth": 100,
                        "cellStyle": {"textAlign": "center"},
                        "headerClass": "header-center-aligned",
                        "pinned": "left",
                    }
                )
            elif "Max" in column or "Min" in column or column == "amount":
                configs.append(
                    {
                        "autoSize": True,
                        "maxWidth": 150,
                        "cellStyle": {"textAlign": "center"},
                        "headerClass": "header-center-aligned",
                        "pinned": "left",
                    }
                )
            elif column in ["response"]:
                if wraptext:
                    configs.append(
                        {
                            "autoSize": True,
                            "width": 500,
                            "wrapText": True,
                            "autoHeight": True,
                        }
                    )
                else:
                    configs.append(
                        {
                            "flex": 2,
                            "minWidth": 300,
                        }
                    )
            elif column in ["hex(response)"]:
                if wraptext:
                    configs.append(
                        {
                            "autoSize": False,
                            "width": 500,
                            "wrapText": True,
                            "autoHeight": True,
                            "cellStyle": {"font-family": "monospace"},
                            "hide": not showhex
                        }
                    )
                else:
                    configs.append(
                        {
                            "flex": 3,
                            "minWidth": 300,
                            "cellStyle": {"font-family": "monospace"},
                            "hide": not showhex
                        }
                    )
            else:
                configs.append(
                    {
                        "autoSize": True,
                    }
                )

        columnDefs = [
            {"field": field, **config} for field, config in zip(fields, configs)
        ]

        rowstyles = {
            "styleConditions": [
                {
                    "condition": "params.data.color == 'G'",
                    "style": {"backgroundColor": "#d5f5e3"},
                },
                {
                    "condition": "params.data.color == 'R'",
                    "style": {"backgroundColor": "#fadbd8"},
                },
                {
                    "condition": "params.data.color == 'Y'",
                    "style": {"backgroundColor": "#fcf3cf"},
                },
                {
                    "condition": "params.data.color == 'B'",
                    "style": {"backgroundColor": "#d4e6f1"},
                },
                {
                    "condition": "params.data.color == 'M'",
                    "style": {"backgroundColor": "#ebdef0"},
                },
                {
                    "condition": "params.data.color == 'O'",
                    "style": {"backgroundColor": "#fae5d3"},
                },
                {
                    "condition": "params.data.color == 'Z'",
                    "style": {"backgroundColor": "#d6dbdf"},
                },
            ],
            "defaultStyle": {"backgroundColor": "white", "color": "black"},
        }

        if wraptext:
            resize_strategy = {"type": "fitGridWidth"}
        else:
            resize_strategy = {"type": "fitCellContents"}

        # a new id per grid configuration makes the grid remount and re-request its rows
        grid_id = {"type": "data-grid", "index": f"{int(bool(squeeze))}-{int(bool(showhex))}-{int(bool(wraptext))}-{response_s}-{response_e}"}

        data = AgGrid(
            id=grid_id,
            columnDefs=columnDefs,
            rowModelType="infinite",
            defaultColDef={
                "resizable": True,
                "sortable": True,
                "filter": True,
                "checkboxSelection": False,
            },
            className="ag-theme-quartz",
            getRowStyle=rowstyles,
            dashGridOptions={
                "pagination": True,
                "paginationPageSize": _PAGE_SIZE,
                "cacheBlockSize": _PAGE_SIZE,
                "maxBlocksInCache": 10,
                "rowBuffer": 0,
                "animateRows": False,
                "alwaysShowHorizontalScroll": True,
                "autoSizeStrategy": resize_strategy,
                "ensureDomOrder": True,


                "enableCellTextSelection": True,
                "enableRangeSelection": True,
                "cellSelection": True,
                "suppressCopyRowsToClipboard": False,
                "suppressCopySingleCellRanges": False,
            },
            style={"height": "1000px"}

        )

        return data

    # callback rows of the data grid; requested page by page by AG Grid
    @app.callback(
        Output({"type": "data-grid", "index": MATCH}, "getRowsResponse"),
        Input({"type": "data-grid", "index": MATCH}, "getRowsRequest"),
        State("config-store", "data"),
        State("switch-squeezedata", "value"),
        State("response_s", "value"),
        State("response_e", "value"),
        prevent_initial_call=True,
    )
    def get_rows(request, store, squeeze, response_s, response_e):
        if request is None:
            raise PreventUpdate

        config = AnalyzerConfig(**store)
        if not config.database:
            raise PreventUpdate

        table = get_table(get_dataset(config), squeeze, response_s, response_e)
        rows, count = table.page(
            request.get("startRow", 0),
            request.get("endRow", _PAGE_SIZE),
            request.get("sortModel") or [],
            request.get("filterModel") or {},
        )
        return {"rowData": rows, "rowCount": count}

    @app.callback(
        Output("query-output", "children"),
        Input("query-button", "n_clicks"),
        Input("select-input", "n_submit"),
        Input("where-input", "n_submit"),
        State("select-input", "value"),
        State("where-input", "value"),
        State('config-store', 'data'),
        prevent_initial_call=True
    )
    def execute_query(n_clicks, n_submit1, n_submit2, select_cols, where_condition, data):
        
        con = sqlite3.connect(f"{data['directory']}/{data['database']}")
        con.create_function("match_string", 2, match_string)
        con.create_function("match_hex", 2, match_hex)

        if not where_condition:
            query = f"SELECT {select_cols} FROM experiments"
        else:
            query = f"SELECT {select_cols} FROM experiments WHERE {where_condition}"

        # Build query; only the first 100 rows are shown, so only fetch those
        try:
            df = pd.read_sql(f"SELECT * FROM ({query}) LIMIT 100;", con)
        except Exception as e:
            print(e)
            return f"ERROR: {e}"
        finally:
            con.close()

        return df.to_string(index=False)


#
# Layout
#

def callback_func1(value):
    # do something
    return result1

def callback_func2(value):
    # do something else
    return result2

def callback_func3(value):
    return result3

def create_layout(app):

    app.layout = html.Div([
        dcc.Store(id="config-store", data=asdict(_config)),
        html.Div(
            [
                html.H4("Research by Raelize"),
            ],
            style={"width": "80%", "border-style": "none", "margin": "0 auto"},
        ),
        html.Div([
            dcc.Tabs(id="tabs", value='tab-graph', children=[
                dcc.Tab(label='Graph', value='tab-graph'),
                dcc.Tab(label='Data', value='tab-data'),
                dcc.Tab(label='Information', value='tab-information'),
                dcc.Tab(label='Database', value='tab-database'),
                dcc.Tab(label='Guide', value='tab-guide'),
            ]),
        ],
        style={"width": "80%", "border-style": "none", "margin": "0 auto"},
        ),

        html.Div(id='tabs-content')
    ], 
        style={
            "width": "100%",
            "border-style": "none",
            "margin-top": "100px",
            "margin-bottom": "100px",
        },
    
    )

    @app.callback(
        Output('tabs-content', 'children'),
        Input('tabs', 'value'),
        State('config-store', 'data')
    )
    def render_content(tab, store):
        if tab == 'tab-graph':

            # apply state when available
            database = store.get('database') if store else None
            x = store.get('x') if store else None
            y = store.get('y') if store else None

            content = html.Div([
                dbc.Card(
                    dbc.CardBody(
                        [
                            html.Div(
                                [
                                    html.Button(
                                        "Update",
                                        id="update-button",
                                        n_clicks=0,
                                        style={"width": "100px"},
                                    ),
                                    html.Datalist(
                                        id="examples",
                                        children=[
                                            html.Option(
                                                value="match_string(response, 'ets')"
                                            ),
                                            html.Option(
                                                value="match_hex(response, '661b')"
                                            ),
                                            html.Option(value="color = 'G'"),
                                            html.Option(value="delay > 100"),
                                            html.Option(value="length > 100"),
                                        ],
                                    ),
                                    dcc.Input(
                                        id="query-input",
                                        type="text",
                                        list="examples",
                                        value="",
                                        style={
                                            "width": "100%",
                                            "display": "inline-block",
                                        },
                                        placeholder="SELECT * FROM experiments WHERE",
                                        persistence=True,
                                    ),
                                ],
                                style={"display": "flex", "alignItems": "center"},
                            )
                        ]
                    )
                ),
                dbc.Card(
                    dbc.CardBody(
                        [
                            dcc.Dropdown(
                                id="database-dropdown",
                                style={"width": "100%"},
                                options=get_databases(_config.directory),
                                placeholder="database",
                                value=_config.database or database,
                            ),
                            html.Div(
                                [
                                    dcc.Dropdown(
                                        id="x-dropdown",
                                        style={"width": "100%"},
                                        placeholder="x-axis",
                                        value=_config.x or x,
                                    ),
                                    dcc.Dropdown(
                                        id="y-dropdown",
                                        style={"width": "100%"},
                                        placeholder="y-axis",
                                        value=_config.y or y,
                                    ),
                                    dcc.Input(
                                        id="jitter-input",
                                        type="number",
                                        value=0,
                                        style={"width": "100%"},
                                    ),
                                ],
                                style=dict(display="flex"),
                            ),
                            
                            dcc.Dropdown(
                                id="graph-dropdown",
                                style={"width": "100%"},
                                options=[
                                    {'label': 'scatter', 'value': 'scatter'},\
                                    {'label': 'scatter2', 'value': 'scatter2'},
                                    {'label': 'simple', 'value': 'simple'},
                                ],
                                # placeholder="option1",
                                value='scatter'
                            ),
                        ]
                    )
                ),
                dbc.Card(
                    dbc.CardBody(
                        [
                            html.Center(
                                [
                                    dcc.Graph(
                                        id="graph", 
                                        config={'displayModeBar': True},
                                        style={"width": "80%"}),
                                ]
                            ),
                        ]
                    )
                ),
                dbc.Card(
                    dbc.CardBody(
                        [
                            html.Span(
                                dbc.Switch(
                                    id="switch-fixgreen",
                                    value=True,
                                    label="Do not recolor green experiments",
                                    style={},
                                )
                            ),
                            *(
                                input_component
                                for color in _COLORS
                                for input_component in [
                                    dcc.Input(
                                        id=f"recolor-{color}",
                                        type="text",
                                        placeholder=f"{color}",
                                        style={"width": "15%"},
                                        persistence=True,
                                    ),
                                    dcc.Input(
                                        id=f"recolor-{color}-label",
                                        type="text",
                                        placeholder=f"{color}-label",
                                        style={
                                            "width": "15%",
                                            "margin-right": "10px",
                                            "margin-bottom": "10px",
                                        },
                                        persistence=True,
                                    ),
                                ]
                            ),
                        ]
                    )
                ),
            ],
            style={"width": "80%", "border-style": "none", "margin": "0 auto"},
            )
        elif tab == 'tab-data':
            content = html.Div([
                        dbc.Card(
                            dbc.CardBody([
                                    html.Div([
                                        dbc.Switch(
                                            id="switch-squeezedata",
                                            value=True,
                                            label="Squeeze Data",
                                            className="d-inline-block me-3"
                                        ),
                                        dbc.Switch(
                                            id="switch-showhexdata",
                                            value=False,
                                            label="Show Hex",
                                            className="d-inline-block me-3"
                                        ),
                                        dbc.Switch(
                                            id="switch-wraptext",
                                            value=False,
                                            label="Wrap response",
                                            className="d-inline-block me-3"
                                        ),
                                        dcc.Input(
                                            id="response_s",
                                            type="number",
                                            placeholder="start",
                                            className="d-inline-block me-2",
                                            persistence=True,
                                        ),
                                        dcc.Input(
                                            id="response_e",
                                            type="number",
                                            placeholder="end",
                                            className="d-inline-block me-2",
                                            persistence=True,
                                        ),
                                    ], className="mb-0"),
                                ]
                            )
                        ),
                        dbc.Card(
                            dbc.CardBody([
                                html.Div(
                                        id="data",
                                        className="border-0 p-2 bg-light rounded",
                                        style={
                                            "minHeight": "400px",
                                            "height": "100%",
                                            "i": "100%",
                                            "fontFamily": "monospace",
                                            "whiteSpace": "pre-wrap",
                                            "overflowY": "auto",
                                            "border-style": "none",
                                        }
                                    ),
                            ]),
                        )
                    ],style={"width": "80%", "border-style": "none", "margin": "0 auto"})
            
            return content
        elif tab == 'tab-information':
            content = html.Div([
                dbc.Card(
                    [
                        dbc.CardHeader("Arguments:"),
                        dbc.CardBody(
                            [
                                dcc.Markdown("", id="argv"),
                            ]
                        ),
                    ]
                ),
                dbc.Card(
                    [
                        dbc.CardHeader("Points:"),
                        dbc.CardBody(
                            [
                                dcc.Markdown("", id="points"),
                            ]
                        ),
                    ]
                ),
                dbc.Card(
                    [
                        dbc.CardHeader("Store:"),
                        dbc.CardBody(
                            [
                                dcc.Markdown("", id="printstore"),
                            ]
                        ),
                    ]
                )
            ],style={"width": "80%", "border-style": "none", "margin": "0 auto"})
            return content
        elif tab == 'tab-database':
            content = html.Div([
                dbc.Card([
                    dbc.CardBody([
                        # All on one line
                        html.Div([
                            html.Span("SELECT ", style={"marginRight": "5px", "fontWeight": "bold"}),
                            dbc.Input(
                                id="select-input",
                                type="text",
                                value="*",
                                placeholder="columns",
                                style={"width": "150px", "display": "inline-block", "marginRight": "5px"}
                            ),
                            html.Span(" FROM experiments WHERE ", style={"margin": "0 5px", "fontWeight": "bold"}),
                            dbc.Input(
                                id="where-input",
                                type="text",
                                value="",
                                placeholder="",
                                style={"width": "1200px", "display": "inline-block", "marginRight": "10px"}
                            ),
                            dbc.Button(
                                "Execute",
                                id="query-button",
                                n_clicks=0,
                                color="primary",
                                style={"display": "inline-block"}
                            ),
                            
                        ], style={"display": "flex", "alignItems": "center"}),
                    ])
                ]),
                html.Pre(id='query-output')
            ],style={"width": "80%", "border-style": "none", "margin": "0 auto"})
        elif tab == 'tab-guide':
            with open('pages/guide.md', 'r', encoding='utf-8') as f:
                guide_text = f.read()
            
            content = html.Div([
                dbc.Card([
                    dbc.CardBody([
                         html.Div([
                             dcc.Markdown(guide_text)
                         ])
                    ])
                ])
            ],style={"width": "80%", "border-style": "none", "margin": "0 auto"})

                             

        return content

def check_env() -> None:
    required = ["ANALYZER_DIRECTORY"]
    missing = [var for var in required if var not in os.environ]
    if missing:
        raise ValueError(f"Missing required environment variables: {missing}")


#
# App
#

# Note: i've added suppress_callback_exceptions=True here as with tabs some elements may be not present.
# Another option would be to use a validated layout which prevents the callback exceptions.

app = Dash(__name__, suppress_callback_exceptions=True)
app.css.config.serve_locally = True
app.scripts.config.serve_locally = True
server = app.server

#
# Main
#

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=f"Raelize Glitch Analyzer v{PROJECT_METADATA['version']} - {PROJECT_METADATA['description']}",
        prog="analyzer",
    )
    parser.add_argument("--ip", help="Server port", type=str, default="127.0.0.1")
    parser.add_argument("--port", help="Server port", type=int, default=8000)
    parser.add_argument("directory", nargs="+", help="Database directorys", type=str)
    parser.add_argument("--x", required=False, help="Preset the x parameter")
    parser.add_argument("--y", required=False, help="Preset the y parameter")
    parser.add_argument("--database", required=False, help="Database selected")
    parser.add_argument("--debug", action="store_true", help="Enable debug mode")
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {PROJECT_METADATA['version']}"
    )

    args = parser.parse_args()

    _config.serverip = args.ip
    _config.serverport = args.port
    _config.directory = args.directory[0]
    _config.x = args.x
    _config.y = args.y
    _config.database = args.database 

    register_callbacks(app)
    create_layout(app)

    app.run(host=_config.serverip, port=_config.serverport, debug=args.debug)
else:
    # this path is taken when started with e.g. unicorn
    check_env()
    _config.directory = os.environ.get("ANALYZER_DIRECTORY", "./databases")
    if not Path(_config.directory).absolute().exists():
        raise FileNotFoundError(
            f"Directory {_config.directory} does not exist. Please set the environment variable ANALYZER_DIRECTORY."
        )

    register_callbacks(app)
    create_layout(app)
