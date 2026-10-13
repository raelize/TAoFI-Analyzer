#!/usr/bin/env python3
import argparse
import os
import re
import sqlite3
import sys
import threading
import time
from functools import wraps
from collections import OrderedDict
from contextlib import closing
from dataclasses import asdict, dataclass, field
from operator import itemgetter
from os import listdir
from pathlib import Path
from typing import Dict
from urllib.request import pathname2url
import random

import dash_bootstrap_components as dbc
import numpy as np
import pandas as pd
import plotly.express as px
from dash import ALL, MATCH, Dash, Input, Output, State, dcc, html, set_props
from dash import callback_context as ctx
from dash import no_update
from dash.exceptions import PreventUpdate
from dash_ag_grid import AgGrid
from dataclasses_json import dataclass_json
from flask import has_request_context, request

from graphs.common import COLOR_CODES, RECOLOR_FLAGS

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
    dbkey: str = ""
    graph: str = "scatter"
    fixgreen: bool = True
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
_COUNT_CACHE = {}  # path -> (file_key, number of experiments)
_CACHE_LOCK = threading.Lock()
_RECOLOR_CACHE_SIZE = 8
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
    """Identity of a database file: changes whenever the file is (re)written.

    In WAL mode new experiments are written to the -wal file first (the
    database file itself only changes at a checkpoint), so that counts too.
    """
    st = os.stat(path)
    key = (os.path.abspath(path), st.st_mtime_ns, st.st_size)
    try:
        wal = os.stat(path + "-wal")
        key += (wal.st_mtime_ns, wal.st_size)
    except FileNotFoundError:
        pass
    return key


def is_local_request():
    """True when the current request comes from this machine."""
    return has_request_context() and request.remote_addr in ("127.0.0.1", "::1")


def data_directory(directory):
    """Directory to read databases from, or None if ``directory`` is not a directory.

    Only local users may pick another directory than the one the analyzer was
    started with; for everyone else the server side ``_config.directory`` is used.
    """
    if not directory or not is_local_request():
        return _config.directory
    directory = os.path.abspath(os.path.expanduser(directory))
    if not os.path.isdir(directory):
        return None
    return directory


def database_path(directory, database):
    """Path of ``database`` in ``directory``, or None if it is not one of its databases.

    The database name comes from the browser, so it must be a plain ``*.sqlite``
    file name; the directory is checked by data_directory().
    """
    directory = data_directory(directory)
    if directory is None:
        return None
    if not database or os.path.basename(database) != database or not database.endswith(".sqlite"):
        return None
    path = os.path.join(directory, database)
    if not os.path.isfile(path):
        return None
    return path


def list_directory(directory):
    """Subdirectories (name, number of databases) and number of databases in ``directory``."""
    subdirectories = []
    databases = 0
    try:
        entries = sorted(os.scandir(directory), key=lambda e: e.name.lower())
    except OSError:
        return [], 0
    for entry in entries:
        if entry.name.startswith("."):
            continue
        try:
            if entry.is_dir():
                try:
                    count = sum(1 for name in os.listdir(entry.path) if name.endswith(".sqlite"))
                except OSError:
                    count = 0
                subdirectories.append((entry.name, count))
            elif entry.name.endswith(".sqlite"):
                databases += 1
        except OSError:
            continue
    return subdirectories, databases


def connect(path):
    """Read-only connection to a database (never creates or modifies a file)."""
    uri = f"file:{pathname2url(os.path.abspath(path))}?mode=ro"
    return sqlite3.connect(uri, uri=True)


# columns the analyzer needs in the experiments table
REQUIRED_COLUMNS = ("color", "response")


def get_number_of_experiments(directory, database):
    """Number of experiments, or None if the file is not an analyzer database."""
    database_path = os.path.join(directory, database)

    try:
        key = file_key(database_path)
        cached = _COUNT_CACHE.get(key[0])
        if cached is not None and cached[0] == key:
            return cached[1]
        with closing(connect(database_path)) as connection:
            with closing(connection.cursor()) as cursor:
                cursor.execute("SELECT name FROM pragma_table_info('experiments')")
                columns = {row[0] for row in cursor.fetchall()}
                if not all(column in columns for column in REQUIRED_COLUMNS):
                    count = None
                else:
                    cursor.execute("SELECT COUNT(*) FROM experiments")
                    count = cursor.fetchone()[0]
        # one entry per file (not per version), so a growing database doesn't grow the cache
        _COUNT_CACHE[key[0]] = (key, count)
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

    # transform to options; other sqlite files are shown, but can't be selected
    databases_options = []
    for database in databases:
        count = get_number_of_experiments(directory, database)
        if count is None:
            databases_options.append(
                {"label": f"{database} (not an analyzer database)", "value": database, "disabled": True}
            )
        else:
            databases_options.append({"label": f"{database} ({count})", "value": database})

    return databases_options


def get_argv(database_path):
    try:
        with closing(connect(database_path)) as connection:
            with closing(connection.cursor()) as cursor:
                cursor.execute("SELECT argv FROM metadata")
                argvstr = cursor.fetchone()[0]
                return argvstr
    except Exception as e:
        print("ERROR (get_argv): %s" % (e))


def get_parameters(database_path):
    try:
        with closing(connect(database_path)) as connection:
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
        self.base_colors.flags.writeable = False
        self._mask_cache = {}
        self._recolor_cache = OrderedDict()
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
        """Apply the recolor fields in order (same semantics as the old per-record loop).

        The result only depends on the arguments (never on what an earlier
        request did), so the graph and the data tab agree on every worker.
        """
        cache_key = (tuple(regexes), tuple(color_codes), bool(fixgreen), flags)
        with self._lock:
            colors = self._recolor_cache.get(cache_key)
            if colors is not None:
                self._recolor_cache.move_to_end(cache_key)
                return colors

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
        colors.flags.writeable = False
        with self._lock:
            self._recolor_cache[cache_key] = colors
            while len(self._recolor_cache) > _RECOLOR_CACHE_SIZE:
                self._recolor_cache.popitem(last=False)
        return colors

    @staticmethod
    def color_setting(config):
        """The parts of ``config`` that decide the colors: (regexes, fixgreen, flags) or None."""
        flags = RECOLOR_FLAGS.get(config.graph)
        if flags is None:
            # graph type without recoloring (e.g. simple)
            return None
        regexes = tuple(config.colors.get(color, [None, None])[0] for color in _COLORS)
        return (regexes, bool(config.fixgreen), flags)

    def colors_for(self, config):
        """Colors as shown by the graph for ``config`` (graph type, fixgreen and recolor fields)."""
        setting = self.color_setting(config)
        if setting is None:
            return self.base_colors
        regexes, fixgreen, flags = setting
        return self.recolor(regexes, COLOR_CODES, fixgreen, flags)


def load_dataframe(database_path, query):
    con = connect(database_path)

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
    """Cached, loaded database for the given database/query."""
    path = database_path(config.directory, config.database)
    if path is None:
        raise PreventUpdate

    key = file_key(path) + (config.query or "",)

    with _CACHE_LOCK:
        dataset = _DATASET_CACHE.get(key)
        if dataset is not None:
            _DATASET_CACHE.move_to_end(key)
            return dataset

    try:
        df = load_dataframe(path, config.query or "")
    except Exception as e:
        print("ERROR (get_dataset): %s" % (e))
        raise PreventUpdate

    missing = [column for column in REQUIRED_COLUMNS if column not in df.columns]
    if missing:
        print("ERROR (get_dataset): %s has no %s column" % (config.database, ", ".join(missing)))
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

    def __init__(self, df, responses, squeezed, numeric=()):
        self.df = df
        self.responses = responses
        self.squeezed = squeezed
        # numeric columns (squeezed: the min/max columns and the amount)
        self.numeric = set(numeric)
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


def build_table(dataset, colors, squeeze_records, response_s, response_e):
    """Data tab table with the columns of the database (in database order).

    Squeezed, numeric (int/float) columns become a "<column> >" (min) and
    "<column> <" (max) pair; other columns show the value of the first experiment.
    """
    df = dataset.df
    v = dataset.names

    def shown(column):
        if column in (v.response, "amount", "rlen", "hex(response)"):
            return False
        # skip columns without any value (unless there are no rows at all)
        return df.empty or bool(df[column].notna().any())

    def values(column):
        if column == v.color:
            # as colored in the graph
            return colors
        values = df[column].to_numpy()
        if values.dtype == object:
            # other blobs can't be sent to the grid as is
            values = np.array([r.hex(" ") if isinstance(r, bytes) else r for r in values], dtype=object)
        return values

    def numeric(column):
        return column != v.color and df[column].dtype.kind in "iuf"

    columns = [column for column in df.columns if shown(column)]

    # slice response
    responses = df[v.response].to_numpy(dtype=object)
    if response_s is not None or response_e is not None:
        responses = np.array(
            [slice_response(r, response_s, response_e) for r in responses], dtype=object
        )

    if not squeeze_records:
        out = pd.DataFrame({column: values(column) for column in columns})
        return Table(out, responses, squeezed=False, numeric={c for c in columns if numeric(c)})

    grouped = pd.DataFrame({column: values(column) for column in columns})
    grouped["response"] = responses
    aggregations = {"amount": ("response", "size")}
    ranges = set()
    for column in columns:
        if numeric(column):
            aggregations[f"{column} >"] = (column, "min")
            aggregations[f"{column} <"] = (column, "max")
            ranges |= {f"{column} >", f"{column} <"}
        else:
            aggregations[column] = (column, "first")

    squeezed = grouped.groupby("response", sort=False).agg(**aggregations)
    squeezed = squeezed.sort_values("amount", ascending=False, kind="stable")
    responses = squeezed.index.to_numpy(dtype=object)
    return Table(squeezed.reset_index(drop=True), responses, squeezed=True, numeric=ranges | {"amount"})


def get_table(dataset, config, squeeze_records, response_s, response_e):
    """Cached table for the data tab, colored as the graph for ``config``."""
    key = (dataset.key, Dataset.color_setting(config), bool(squeeze_records), response_s, response_e)
    with _CACHE_LOCK:
        table = _TABLE_CACHE.get(key)
        if table is not None:
            _TABLE_CACHE.move_to_end(key)
            return table
    table = build_table(dataset, dataset.colors_for(config), squeeze_records, response_s, response_e)
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


_LAST_TIMING = {}


def timed(label, base=None):
    """Report how long a content-generating callback took in the header badge.

    With ``base`` the badge also keeps showing the time of that earlier step,
    e.g. the data table build next to the time of fetching one page of rows.
    """

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            started = time.perf_counter()
            result = func(*args, **kwargs)
            elapsed = time.perf_counter() - started
            _LAST_TIMING[label] = elapsed
            shown = label
            if base in _LAST_TIMING:
                # e.g. the data table build plus the first page of rows
                elapsed += _LAST_TIMING.pop(base)
                shown = base
            # the client-side timer is filled in by assets/timing.js
            set_props("timing-label", {"children": f"{shown}: "})
            set_props("timing", {"children": f"server ({elapsed:.2f} s)"})
            return result

        return wrapper

    return decorator


#
# Callbacks
#


def register_callbacks(app):
    # callback for remembering the zoomed area of the graph (shown on the information tab)
    @app.callback(
        Output("zoom-store", "data"),
        Input("graph", "relayoutData"),
        Input("config-store", "data"),
        State("zoom-store", "data"),
        prevent_initial_call=True,
    )
    def update_zoom(relayout, store, zoom):
        if ctx.triggered_id == "config-store":
            # another graph: nothing zoomed (yet)
            return None
        if not relayout:
            raise PreventUpdate

        zoom = dict(zoom or {})
        for axis in ("xaxis", "yaxis"):
            if relayout.get(f"{axis}.autorange"):
                zoom.pop(axis, None)
            elif f"{axis}.range[0]" in relayout:
                zoom[axis] = [relayout[f"{axis}.range[0]"], relayout[f"{axis}.range[1]"]]
            elif f"{axis}.range" in relayout:
                zoom[axis] = list(relayout[f"{axis}.range"])
        return zoom or None

    # callback for printing the zoomed area
    @app.callback(
        Output("points", "children"),
        Input("zoom-store", "data"),
        State("config-store", "data"),
    )
    def zoomed_points(zoom, store):
        if not zoom:
            return "Zoom into the graph to see the selected range here."

        names = {"xaxis": store.get("x"), "yaxis": store.get("y")}
        p = ""
        for axis in ("xaxis", "yaxis"):
            if axis in zoom:
                low, high = (f"{v:g}" if isinstance(v, (int, float)) else str(v) for v in zoom[axis])
                p += f"* {names[axis]}: {low} – {high}\n"
            else:
                p += f"* {names[axis]}: (not zoomed)\n"
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
        Output("directory-input", "invalid"),
        Input("update-button", "n_clicks"),
        Input("refresh-timer", "n_intervals"),
        Input("directory-input", "value"),
    )
    def update_database_list(nr_of_clicks, n_intervals, directory):
        directory = data_directory(directory)
        if directory is None:
            return [], True
        return get_databases(directory), False

    # callback for the directory browser: open/close it, walk the tree, pick a directory
    @app.callback(
        Output("directory-modal", "is_open"),
        Output("browse-directory", "data"),
        Output("directory-input", "value"),
        Input("directory-browse", "n_clicks"),
        Input("browse-select", "n_clicks"),
        Input("browse-cancel", "n_clicks"),
        Input({"type": "browse-entry", "index": ALL}, "n_clicks"),
        State("directory-input", "value"),
        State("browse-directory", "data"),
        prevent_initial_call=True,
    )
    def browse_directory(browse, select, cancel, entries, directory, browsed):
        if not is_local_request():
            raise PreventUpdate

        trigger = ctx.triggered_id
        if trigger == "directory-browse":
            start = data_directory(directory) or _config.directory
            return True, os.path.abspath(start), no_update
        if trigger == "browse-cancel":
            return False, no_update, no_update
        if trigger == "browse-select":
            return False, no_update, browsed
        if isinstance(trigger, dict) and ctx.triggered[0]["value"]:
            # entries are re-created (n_clicks=0) every time the list changes; only real clicks count
            return no_update, os.path.abspath(trigger["index"]), no_update
        raise PreventUpdate

    # callback for the contents of the directory browser
    @app.callback(
        Output("browse-path", "children"),
        Output("browse-list", "children"),
        Input("browse-directory", "data"),
        prevent_initial_call=True,
    )
    def update_browser(directory):
        if not directory or not is_local_request():
            raise PreventUpdate

        subdirectories, databases = list_directory(directory)
        parent = os.path.dirname(directory)
        items = []
        if parent != directory:
            items.append(
                dbc.ListGroupItem(
                    "..", id={"type": "browse-entry", "index": parent}, action=True, n_clicks=0
                )
            )
        for name, count in subdirectories:
            items.append(
                dbc.ListGroupItem(
                    [
                        name,
                        dbc.Badge(count, color="primary" if count else "light", text_color=None if count else "secondary", className="ms-2"),
                    ],
                    id={"type": "browse-entry", "index": os.path.join(directory, name)},
                    action=True,
                    n_clicks=0,
                )
            )
        path = [html.Code(directory), html.Span(f"  ({databases} databases here)", className="text-muted")]
        return path, items

    # callback for the auto refresh timer
    @app.callback(
        Output("refresh-timer", "disabled"),
        Output("refresh-timer", "interval"),
        Input("switch-autorefresh", "value"),
        Input("refresh-seconds", "value"),
    )
    def update_refresh_timer(enabled, seconds):
        seconds = max(1, int(seconds or 10))
        return not enabled, seconds * 1000

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
            Input("refresh-timer", "n_intervals"),
            Input("switch-fixgreen", "value"),
            Input("graph-dropdown", "value"),
            Input("directory-input", "value"),
        ],
        State("query-input", "value"),
        State("config-store", "data"),
        State("jitter-input", "value"),
        [State(f"recolor-{color}", "value") for color in _COLORS]
        + [State(f"recolor-{color}-label", "value") for color in _COLORS],
    )
    # def update_store(nr_of_clicks, contents, query, database, x, y, store, *color_states):
    def update_store(nr_of_clicks, database, x, y, n_intervals, fixgreen, graph, directory, query, store, jitter, *color_states):
        config = AnalyzerConfig(**store)

        directory = data_directory(directory)
        if directory is None:
            raise PreventUpdate

        if ctx.triggered_id == "directory-input" and directory != config.directory:
            # another directory: its databases are listed by update_database_list(), start over
            config.directory = directory
            config.database = None
            config.dbkey = ""
            config.argv = ""
            return asdict(config), None, config.x, config.y

        if database is None:
            raise PreventUpdate

        # check if database exists
        path = database_path(directory, database)
        if path is None:
            print("database does not exist")
            raise PreventUpdate

        # auto refresh: only re-render when the database file has changed
        dbkey = repr(file_key(path))
        if ctx.triggered_id == "refresh-timer" and dbkey == config.dbkey:
            raise PreventUpdate
        config.dbkey = dbkey

        config.directory = directory
        config.database = database
        config.x = x
        config.y = y
        config.jitter = jitter
        config.query = query
        config.fixgreen = fixgreen
        config.graph = graph
        config.argv = get_argv(path)

        # Update color in config
        for color, regex, label in zip(_COLORS, color_states[:8], color_states[8:]):
            config.colors[color] = [regex, label]

        return asdict(config), config.database, config.x, config.y

    # callback for printing the argv string at the bottom; also runs when the
    # information tab is opened (its input already exists, only the output is new)
    @app.callback(
        Output("argv", "children"),
        Input("config-store", "data"),
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
        path = database_path(store.get("directory"), database)
        if path is None:
            raise PreventUpdate
        return get_parameters(path)

    # callback for y list
    @app.callback(
        Output("y-dropdown", "options"),
        Input("database-dropdown", "value"),
        State("config-store", "data"),
        prevent_initial_call=True,
    )
    def update_dropdown_y(database, store):
        path = database_path(store.get("directory"), database)
        if path is None:
            raise PreventUpdate
        return get_parameters(path)



    # callback graph; chained from update_store()
    @app.callback(
        Output("graph", "figure"),
        [
            Input("config-store", "data"),
            Input("x-dropdown", "value"),
            Input("y-dropdown", "value"),
            Input("switch-hover", "value"),
        ],
        prevent_initial_call=True,
    )
    @timed("graph")
    def update_graph(store, x, y, hover):
        from graphs.scatter import scatter_func
        from graphs.scatter2 import scatter2_func
        from graphs.simple import simple_func
        
        CALLBACKS = {
            'scatter': scatter_func,
            'scatter2': scatter2_func,
            'simple': simple_func
        }

        config = AnalyzerConfig(**store)

        # the recolor fields as stored by update_store(), so that the data tab
        # (which colors from the store as well) shows the same colors
        regexes = [config.colors.get(color, [None, None])[0] for color in _COLORS]
        labels = [config.colors.get(color, [None, None])[1] for color in _COLORS]

        if ctx.triggered_id == "config-store":
            # update x and y
            x = config.x
            y = config.y

        # prevent update
        if any(v is None for v in [x, y]):
            raise PreventUpdate

        dataset = get_dataset(config)

        # create graph
        if config.graph in CALLBACKS:
            callback_func = CALLBACKS[config.graph]
            result = callback_func(x, y, config.fixgreen, dataset, config, _COLORS, *regexes, *labels)
            if not hover:
                # no hover lookups or hover labels at all (cheaper on huge plots)
                result.update_layout(hovermode=False)
            return result
        raise PreventUpdate

    # callback data; chained from update_graph()
    @app.callback(
        Output("data", "children"),
        Output("data-columns", "options"),
        Output("data-columns", "value"),
        [
            # Input("config-store", "data"),
            # Input("graph", "figure"),
            Input("switch-squeezedata", "value"),
            Input("switch-wraptext", "value"),
            Input("response_s", "value"),
            Input("response_e", "value"),
            Input('tabs', 'value')
        ],
        State("config-store", "data"),
        State("data-hidden-columns", "data"),
        prevent_initial_call=False,
    )
    @timed("data")
    def update_data(squeeze, wraptext, response_s, response_e, tab, store, hidden):
        config = AnalyzerConfig(**store)

        if not config.database:
            raise PreventUpdate

        # squeeze data (or not); only the column layout is needed here, the
        # rows are fetched page by page by get_rows()
        table = get_table(get_dataset(config), config, squeeze, response_s, response_e)
        columns = table.columns

        fields = []
        configs = []

        for column in columns:
            fields.append(column)
            if column in table.numeric and table.squeezed:
                # min/max columns (and amount)
                configs.append(
                    {
                        "autoSize": True,
                        "maxWidth": 150,
                        "cellStyle": {"textAlign": "center"},
                        "headerClass": "header-center-aligned",
                        "pinned": "left",
                    }
                )
            elif column in table.numeric or column in ["color", "rlen"]:
                configs.append(
                    {
                        "autoSize": True,
                        "maxWidth": 100,
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
                        }
                    )
                else:
                    configs.append(
                        {
                            "flex": 3,
                            "minWidth": 300,
                            "cellStyle": {"font-family": "monospace"},
                        }
                    )
            else:
                configs.append(
                    {
                        "autoSize": True,
                    }
                )

        hidden = set(hidden or [])
        columnDefs = [
            {"field": field, **config, "hide": field in hidden} for field, config in zip(fields, configs)
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
        grid_id = {"type": "data-grid", "index": f"{int(bool(squeeze))}-{int(bool(wraptext))}-{response_s}-{response_e}"}

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

        shown = [column for column in columns if column not in hidden]
        return data, list(columns), shown

    # callback for the column selector of the data tab: show/hide columns without rebuilding the grid
    @app.callback(
        Output({"type": "data-grid", "index": ALL}, "columnDefs"),
        Output("data-hidden-columns", "data"),
        Input("data-columns", "value"),
        State("data-columns", "options"),
        State({"type": "data-grid", "index": ALL}, "columnDefs"),
        State("data-hidden-columns", "data"),
        prevent_initial_call=True,
    )
    def update_data_columns(shown, columns, grids, hidden):
        if shown is None or not columns:
            raise PreventUpdate

        # keep columns hidden that this table doesn't have (e.g. squeezed only columns)
        hidden = {column for column in (hidden or []) if column not in columns}
        hidden |= {column for column in columns if column not in shown}

        grids = [
            [{**definition, "hide": definition["field"] in hidden} for definition in definitions]
            for definitions in grids
        ]
        return grids, sorted(hidden)

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
    @timed("rows", base="data")
    def get_rows(request, store, squeeze, response_s, response_e):
        if request is None:
            raise PreventUpdate

        config = AnalyzerConfig(**store)
        if not config.database:
            raise PreventUpdate

        table = get_table(get_dataset(config), config, squeeze, response_s, response_e)
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
    @timed("query")
    def execute_query(n_clicks, n_submit1, n_submit2, select_cols, where_condition, data):
        path = database_path(data.get("directory"), data.get("database"))
        if path is None:
            return "ERROR: select a database on the Graph tab first"

        con = connect(path)
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
        dcc.Store(id="data-hidden-columns", data=["hex(response)"], storage_type="local"),
        dcc.Store(id="zoom-store"),
        html.Div(
            [
                html.H4("Research by Raelize"),
                html.Span(
                    [
                        html.Span("", id="timing-label"),
                        html.Span("", id="timing-client", title="time from sending the request until the browser rendered the result"),
                        html.Span("", id="timing", title="time spent in the server callback"),
                    ],
                    style={"fontFamily": "monospace", "color": "#666"},
                ),
            ],
            style={
                "width": "80%",
                "border-style": "none",
                "margin": "0 auto",
                "display": "flex",
                "justifyContent": "space-between",
                "alignItems": "baseline",
            },
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
    @timed("tab")
    def render_content(tab, store):
        if tab == 'tab-graph':

            # apply state when available (the CLI/env presets are the initial store)
            database = store.get('database') if store else None
            x = store.get('x') if store else None
            y = store.get('y') if store else None
            graph = store.get('graph', 'scatter') if store else 'scatter'
            local = is_local_request()
            directory = (store.get('directory') if store and local else None) or _config.directory
            fixgreen = store.get('fixgreen', True) if store else True
            jitter = (store.get('jitter') if store else None) or 0

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
                                    dbc.Switch(
                                        id="switch-autorefresh",
                                        value=False,
                                        label="Auto refresh",
                                        className="ms-3 mb-0 text-nowrap",
                                        persistence=True,
                                    ),
                                    dcc.Input(
                                        id="refresh-seconds",
                                        type="number",
                                        value=10,
                                        min=1,
                                        step=1,
                                        placeholder="sec",
                                        style={"width": "70px", "marginLeft": "5px"},
                                        persistence=True,
                                    ),
                                    dcc.Interval(id="refresh-timer", interval=10000, disabled=True),
                                ],
                                style={"display": "flex", "alignItems": "center"},
                            )
                        ]
                    )
                ),
                dbc.Card(
                    dbc.CardBody(
                        [
                            # only local users may pick another directory (see data_directory())
                            dbc.InputGroup(
                                [
                                    dbc.InputGroupText("directory"),
                                    dbc.Input(
                                        id="directory-input",
                                        type="text",
                                        value=directory,
                                        debounce=True,
                                        disabled=not local,
                                        placeholder="directory with databases (press enter to apply)",
                                    ),
                                    dbc.Button(
                                        "Browse…",
                                        id="directory-browse",
                                        n_clicks=0,
                                        color="secondary",
                                        disabled=not local,
                                    ),
                                ],
                                size="sm",
                                className="mb-2",
                            ),
                            dcc.Store(id="browse-directory"),
                            dbc.Modal(
                                [
                                    dbc.ModalHeader(dbc.ModalTitle("Select a directory")),
                                    dbc.ModalBody(
                                        [
                                            html.Div(id="browse-path", className="mb-2"),
                                            dbc.ListGroup(
                                                id="browse-list",
                                                style={"maxHeight": "60vh", "overflowY": "auto"},
                                            ),
                                        ]
                                    ),
                                    dbc.ModalFooter(
                                        [
                                            dbc.Button("Cancel", id="browse-cancel", n_clicks=0, color="secondary"),
                                            dbc.Button("Select", id="browse-select", n_clicks=0, color="primary"),
                                        ]
                                    ),
                                ],
                                id="directory-modal",
                                is_open=False,
                                size="lg",
                                scrollable=True,
                            ),
                            dcc.Dropdown(
                                id="database-dropdown",
                                style={"width": "100%"},
                                options=get_databases(data_directory(directory) or _config.directory),
                                placeholder="database",
                                value=database,
                            ),
                            html.Div(
                                [
                                    dcc.Dropdown(
                                        id="x-dropdown",
                                        style={"width": "100%"},
                                        placeholder="x-axis",
                                        value=x,
                                    ),
                                    dcc.Dropdown(
                                        id="y-dropdown",
                                        style={"width": "100%"},
                                        placeholder="y-axis",
                                        value=y,
                                    ),
                                    dcc.Input(
                                        id="jitter-input",
                                        type="number",
                                        value=jitter,
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
                                value=graph,
                            ),
                        ]
                    )
                ),
                dbc.Card(
                    dbc.CardBody(
                        [
                            html.Center(
                                [
                                    dcc.Loading(
                                        dcc.Graph(
                                            id="graph",
                                            config={'displayModeBar': True},
                                            style={"width": "80%"}),
                                        type="circle",
                                        delay_show=150,
                                        overlay_style={"visibility": "visible", "opacity": 0.4},
                                    ),
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
                                    value=fixgreen,
                                    label="Do not recolor green experiments",
                                    className="d-inline-block me-3",
                                )
                            ),
                            html.Span(
                                dbc.Switch(
                                    id="switch-hover",
                                    value=True,
                                    label="Show hover info on the graph",
                                    className="d-inline-block me-3",
                                )
                            ),
                            html.Div(
                                [
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
                                ],
                                className="mt-2",
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
                                    dcc.Dropdown(
                                        id="data-columns",
                                        multi=True,
                                        placeholder="columns to show",
                                        className="mt-2",
                                    ),
                                ]
                            )
                        ),
                        dbc.Card(
                            dbc.CardBody([
                                dcc.Loading(
                                    type="circle",
                                    delay_show=150,
                                    overlay_style={"visibility": "visible", "opacity": 0.4},
                                    children=html.Div(
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
                dcc.Loading(html.Pre(id='query-output'), type="circle", delay_show=150)
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
    _config.directory = os.path.abspath(args.directory[0])
    _config.x = args.x
    _config.y = args.y
    _config.database = args.database 

    register_callbacks(app)
    create_layout(app)

    app.run(host=_config.serverip, port=_config.serverport, debug=args.debug)
else:
    # this path is taken when started with e.g. unicorn
    check_env()
    _config.directory = os.path.abspath(os.environ.get("ANALYZER_DIRECTORY", "./databases"))
    if not Path(_config.directory).absolute().exists():
        raise FileNotFoundError(
            f"Directory {_config.directory} does not exist. Please set the environment variable ANALYZER_DIRECTORY."
        )
    # same defaults as the command line (None, not ""), optionally preset via env
    _config.database = os.environ.get("ANALYZER_DATABASE")
    _config.x = os.environ.get("ANALYZER_X")
    _config.y = os.environ.get("ANALYZER_Y")

    register_callbacks(app)
    create_layout(app)

    # generate the index page once now: this registers the component script
    # bundles. With several gunicorn workers a worker may otherwise be asked
    # for a bundle before it has served an index page, which fails with
    # "dash is not a registered library" and leaves the browser with a blank app.
    with app.server.test_request_context():
        app._setup_server()
        app.index()
