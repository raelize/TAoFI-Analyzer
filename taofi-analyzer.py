#!/usr/bin/env python3
import argparse
import base64
import os
import re
import sqlite3
import sys
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from os import listdir
from typing import Any, Dict, List, NoReturn, Optional

import dash_bootstrap_components as dbc
import numpy as np
import pandas as pd
import plotly.express as px
from dash import Dash, Input, Output, State, dcc, html
from dash import callback_context as ctx
from dash.exceptions import PreventUpdate
from dash_ag_grid import AgGrid
from dataclasses_json import dataclass_json

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
    query: str = ""
    colors: Dict[str, list] = field(default_factory=dict)
    refresh_interval: int = 0  # In seconds, 0 means disabled


#
# Globals
#

_config = AnalyzerConfig()
_COLORS = ["green", "yellow", "magenta", "orange", "cyan", "blue", "black", "red"]
_COLOR_MAP_CODES = {
    "green": "G",
    "yellow": "Y",
    "magenta": "M",
    "orange": "O",
    "cyan": "C",
    "blue": "B",
    "black": "Z",
    "red": "R",
}

for color in _COLORS:
    _config.colors[color] = [None, None]


#
# Database Helper Functions
#
@contextmanager
def query_db(db_path: str):
    try:
        conn = sqlite3.connect(db_path)
        conn.create_function(
            "match_string", 2, lambda r, t: t.encode(errors="strict") in r
        )
        conn.create_function("match_hex", 2, lambda r, t: bytes.fromhex(t) in r)
        yield conn
    except sqlite3.Error as e:
        print(f"ERROR (query_db): {e}", file=sys.stderr)
        raise PreventUpdate
    finally:
        if "conn" in locals() and conn:
            conn.close()


def get_number_of_experiments(directory: str, database: str) -> int:
    db_path = os.path.join(directory, database)
    try:
        with query_db(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM experiments")
            return cursor.fetchone()[0]
    except Exception:
        return 0


def get_databases(directory: str) -> List[Dict[str, str]]:
    if not directory or not os.path.isdir(directory):
        return []
    databases = sorted(
        [f for f in listdir(directory) if f.endswith(".sqlite")], reverse=True
    )

    options = []
    for db in databases:
        count = get_number_of_experiments(directory, db)
        options.append({"label": f"{db} ({count})", "value": db})
    return options


def get_db_metadata(directory: str, database: str, column: str) -> Optional[str]:
    db_path = os.path.join(directory, database)
    try:
        with query_db(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(f"SELECT {column} FROM metadata")
            result = cursor.fetchone()
            return result[0] if result else None
    except Exception as e:
        print(f"ERROR (get_db_metadata for {column}): {e}", file=sys.stderr)
        return None


def get_parameters(directory: str, database: str) -> List[str]:
    db_path = os.path.join(directory, database)
    try:
        with query_db(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM experiments LIMIT 1")
            parameters = [desc[0] for desc in cursor.description]
            if "response" in parameters:
                parameters.remove("response")
            return parameters
    except Exception as e:
        print(f"ERROR (get_parameters): {e}", file=sys.stderr)
        return []


#
# Data Processing Functions
#


def get_variable_names(columns: List[str]) -> Dict[str, Optional[str]]:
    name_map = {
        "id": ["id"],
        "color": ["color"],
        "normal": ["normal", "normal_voltage"],
        "delay": ["delay", "glitch_delay"],
        "length": ["length", "glitch_length"],
        "voltage": ["voltage", "glitch_voltage"],
        "power": ["power", "glitch_power"],
        "response": ["response"],
        "reset": ["reset"],
    }
    found_names = {}
    for key, potential_names in name_map.items():
        found_names[key] = next(
            (name for name in potential_names if name in columns), None
        )
    return found_names


def recolor_row(
    row: pd.Series, regex: Optional[bytes], new_color: str, fixgreen: bool
) -> str:
    if not regex:
        return row["color"]
    if fixgreen and row["color"] == "G":
        return row["color"]
    if row["response_bytes"] and re.search(regex, row["response_bytes"]):
        return new_color
    return row["color"]


def generate_data_table(
    df: pd.DataFrame,
    squeeze: bool,
    showhex: bool,
    slice_s: Optional[int],
    slice_e: Optional[int],
) -> List[Dict[str, Any]]:
    if df.empty:
        return []

    v = get_variable_names(df.columns)

    # Create a working copy
    df_processed = df.copy()

    df_processed["response_bytes"] = df_processed[v["response"]].apply(
        lambda s: base64.b64decode(s) if isinstance(s, str) else b""
    )

    # Slice the DECODED bytes, not the base64 string
    df_processed["response_sliced"] = df_processed["response_bytes"].str.slice(
        slice_s, slice_e
    )
    df_processed["response_str"] = df_processed["response_sliced"].apply(
        lambda x: x.decode("utf-8", errors="replace")
    )
    if showhex:
        df_processed["hex(response)"] = df_processed["response_sliced"].apply(
            lambda x: x.hex(" ") if x else ""
        )

    if not squeeze:
        cols_to_keep = ["id", "color", "delay", "response_str"]
        if v["normal"]:
            cols_to_keep.append(v["normal"])
        if v["length"]:
            cols_to_keep.append(v["length"])
        if v["power"]:
            cols_to_keep.append(v["power"])
        if v["voltage"]:
            cols_to_keep.append(v["voltage"])
        if v["reset"]:
            cols_to_keep.append(v["reset"])
        if showhex:
            cols_to_keep.append("hex(response)")

        df_processed.rename(columns={"response_str": "response"}, inplace=True)
        return df_processed[
            [c for c in cols_to_keep if c in df_processed.columns]
        ].to_dict("records")
    else:
        agg_dict = {"id": ("count", "amount")}
        param_map = {
            "Delay": v["delay"],
            "Normal": v["normal"],
            "Length": v["length"],
            "Power": v["power"],
            "Voltage": v["voltage"],
            "Reset": v["reset"],
        }

        squeezed = (
            df_processed.groupby(["response_str", "color"])
            .agg(
                amount=("id", "count"),
                **{
                    f"Min({name})": (col, "min")
                    for name, col in param_map.items()
                    if col and col in df_processed.columns
                },
                **{
                    f"Max({name})": (col, "max")
                    for name, col in param_map.items()
                    if col and col in df_processed.columns
                },
            )
            .reset_index()
        )

        if showhex:
            hex_map = df_processed.drop_duplicates(subset=["response_str"])[
                ["response_str", "hex(response)"]
            ]
            squeezed = squeezed.merge(hex_map, on="response_str")

        squeezed.rename(columns={"response_str": "response"}, inplace=True)
        return squeezed.sort_values(by="amount", ascending=False).to_dict("records")


def give_xy_label(parameter: str) -> str:
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


#
# Callbacks
#


def register_callbacks(app):
    # Callback 1: Update config from UI controls
    @app.callback(
        Output("config-store", "data"),
        [
            Input("database-dropdown", "value"),
            Input("x-dropdown", "value"),
            Input("y-dropdown", "value"),
            Input("query-input", "value"),
            Input("jitter-input", "value"),
        ]
        + [Input(f"recolor-{c}", "value") for c in _COLORS]
        + [Input(f"recolor-{c}-label", "value") for c in _COLORS],
        State("config-store", "data"),
    )
    def update_config_store(database, x, y, query, jitter, *color_states_and_store):
        store, color_states = color_states_and_store[-1], color_states_and_store[:-1]
        config = AnalyzerConfig(**store)
        if not ctx.triggered_id:
            raise PreventUpdate
        config.database, config.x, config.y, config.query, config.jitter = (
            database,
            x,
            y,
            query,
            jitter,
        )
        for i, color in enumerate(_COLORS):
            config.colors[color] = [color_states[i], color_states[i + len(_COLORS)]]
        if ctx.triggered_id == "database-dropdown" and database:
            config.argv = get_db_metadata(config.directory, database, "argv")
        return asdict(config)

    # Callback 2: Load data from DB
    @app.callback(
        Output("records-store", "data"),
        [
            Input("update-button", "n_clicks"),
            Input("auto-refresh-interval", "n_intervals"),
        ],
        State("config-store", "data"),
        prevent_initial_call=True,
    )
    def update_records_store(n_clicks, n_intervals, store):
        config = AnalyzerConfig(**store)
        if not all([config.directory, config.database]):
            raise PreventUpdate
        db_path = os.path.join(config.directory, config.database)
        if not os.path.isfile(db_path):
            raise PreventUpdate

        query = "SELECT * FROM experiments" + (
            f" WHERE {config.query}" if config.query else ""
        )
        try:
            with query_db(db_path) as conn:
                df = pd.read_sql_query(query, conn)
        except Exception as e:
            print(
                f"ERROR: Failed to execute query '{query}'. Reason: {e}",
                file=sys.stderr,
            )
            raise PreventUpdate

        if df.empty:
            return []

        if config.jitter > 0:
            for axis in [config.x, config.y]:
                if axis in df.columns and pd.api.types.is_numeric_dtype(df[axis]):
                    df[axis] += np.random.normal(0, config.jitter, df.shape[0])

        # This makes the data JSON serializable for storage in dcc.Store
        if "response" in df.columns:
            df["response"] = df["response"].apply(
                lambda b: base64.b64encode(b).decode("ascii")
                if isinstance(b, bytes)
                else None
            )

        return df.to_dict("records")

    # Callback 3: Update Graph
    @app.callback(
        Output("graph", "figure"),
        Input("records-store", "data"),
        Input("switch-fixgreen", "value"),
        State("config-store", "data"),
        prevent_initial_call=True,
    )
    def update_graph(records_data, fixgreen, store):
        if not records_data:
            return px.scatter(title="No data to display")

        df = pd.DataFrame.from_records(records_data)
        config = AnalyzerConfig(**store)
        x, y = config.x, config.y

        if not all([x, y, x in df.columns, y in df.columns]):
            raise PreventUpdate

        if "response" in df.columns:
            df["response_bytes"] = df["response"].apply(
                lambda s: base64.b64decode(s) if isinstance(s, str) else b""
            )

        # Recolor using the new `response_bytes` column
        df["color_new"] = df["color"]
        for color_name, (regex, _) in config.colors.items():
            if regex:
                compiled_regex = re.compile(regex.encode(errors="strict"))
                color_code = _COLOR_MAP_CODES[color_name]
                # The `recolor_row` function now expects `response_bytes` to exist
                df["color_new"] = df.apply(
                    recolor_row, axis=1, args=(compiled_regex, color_code, fixgreen)
                )

        color_counts = df["color_new"].value_counts().to_dict()
        total_records = len(df)
        fig = px.scatter(
            df,
            x=x,
            y=y,
            render_mode="webgl",
            color="color_new",
            labels={
                "color_new": f"Classification ({total_records:,})",
                x: f"{x} {give_xy_label(x)}",
                y: f"{y} {give_xy_label(y)}",
            },
            color_discrete_map={
                "P": "pink",
                "G": "green",
                "Y": "yellow",
                "M": "magenta",
                "O": "orange",
                "C": "cyan",
                "B": "blue",
                "Z": "black",
                "R": "red",
            },
            category_orders={
                "color_new": ["P", "G", "Y", "M", "O", "C", "B", "Z", "R"]
            },
        )
        fig.update_layout(title_text="", uirevision=config.database)
        legend_labels = {}
        for color_name, (regex, label) in config.colors.items():
            code = _COLOR_MAP_CODES[color_name]
            count = color_counts.get(code, 0)
            if not label:
                label = regex if regex else color_name.capitalize()
            legend_labels[code] = (
                f"{label} ({count:,} / {count / total_records:.1%})"
                if total_records
                else f"{label} (0)"
            )

        p_count = color_counts.get("P", 0)
        legend_labels["P"] = (
            f"Timeout ({p_count:,} / {p_count / total_records:.1%})"
            if total_records
            else "Timeout (0)"
        )

        for entry in fig.data:
            if entry.name in legend_labels:
                entry.name = legend_labels[entry.name]

        return fig

    # Callback 4: Update Data Table
    @app.callback(
        Output("data", "children"),
        Input("records-store", "data"),
        [
            Input("switch-squeezedata", "value"),
            Input("switch-showhexdata", "value"),
            Input("switch-wraptext", "value"),
            Input("response_s", "value"),
            Input("response_e", "value"),
        ],
        prevent_initial_call=True,
    )
    def update_data(records_data, squeeze, showhex, wraptext, s, e):
        if not records_data:
            return "No data available."
        df = pd.DataFrame.from_records(records_data)
        data = generate_data_table(df, squeeze, showhex, s, e)
        if not data:
            return "No results for current settings."
        column_defs = []
        for col in data[0].keys():
            col_def = {"field": col, "autoSize": True}
            if col in ["id", "color", "amount"]:
                col_def.update({"maxWidth": 120, "pinned": "left"})
            elif "Min(" in col or "Max(" in col:
                col_def.update({"maxWidth": 150, "pinned": "left"})
            elif col == "response":
                col_def.update(
                    {"width": 500, "wrapText": wraptext, "autoHeight": wraptext}
                )
            elif col == "hex(response)":
                col_def.update({"width": 500, "hide": not showhex})
            column_defs.append(col_def)

        return AgGrid(
            columnDefs=column_defs,
            rowData=data,
            defaultColDef={"resizable": True, "sortable": True, "filter": True},
            className="ag-theme-quartz",
            dashGridOptions={
                "pagination": True,
                "animateRows": False,
                "autoSizeStrategy": {"type": "fitGridWidth"}
                if wraptext
                else {"type": "fitCellContents"},
                "enableCellTextSelection": True,
                "ensureDomOrder": True,
            },
            style={"height": "1000px"},
        )

    # Other Callbacks
    @app.callback(
        Output("auto-refresh-interval", "disabled"),
        Output("auto-refresh-interval", "interval"),
        Input("toggle-auto-refresh", "value"),
        Input("refresh-interval-input", "value"),
    )
    def manage_auto_refresh(is_on, interval_seconds):
        is_disabled = not is_on
        interval_ms = (interval_seconds or 0) * 1000
        if interval_ms <= 0:
            is_disabled = True
        return is_disabled, interval_ms

    @app.callback(
        Output("database-dropdown", "options"),
        [
            Input("update-button", "n_clicks"),
            Input("auto-refresh-interval", "n_intervals"),
        ],
    )
    def update_database_list(n_clicks, n_intervals):
        return get_databases(_config.directory)

    @app.callback(
        Output("x-dropdown", "options"),
        Output("y-dropdown", "options"),
        Input("database-dropdown", "value"),
        State("config-store", "data"),
    )
    def update_axis_options(database, store):
        if not database:
            raise PreventUpdate
        params = get_parameters(store["directory"], database)
        return params, params

    @app.callback(Output("argv", "children"), Input("config-store", "data"))
    def update_argv(store):
        return store.get("argv", "N/A")


#
# Layout
#
def create_layout(app):
    app.layout = html.Div(
        [
            dcc.Store(id="config-store", data=asdict(_config)),
            dcc.Store(id="records-store", data=[]),
            dcc.Interval(
                id="auto-refresh-interval",
                interval=(_config.refresh_interval or 60) * 1000,
                n_intervals=0,
                disabled=_config.refresh_interval <= 0,
            ),
            html.Div(
                [
                    html.H4("Research by Raelize"),
                    dbc.Card(
                        dbc.CardBody(
                            [
                                dbc.Row(
                                    [
                                        dbc.Col(
                                            html.Button(
                                                "Update",
                                                id="update-button",
                                                n_clicks=0,
                                                className="w-100",
                                            ),
                                            width=2,
                                        ),
                                        dbc.Col(
                                            dcc.Input(
                                                id="query-input",
                                                type="text",
                                                value="",
                                                className="w-100",
                                                placeholder="e.g., color = 'G' AND delay > 100",
                                                persistence=True,
                                            ),
                                            width=10,
                                        ),
                                    ]
                                )
                            ]
                        ),
                        className="mb-3",
                    ),
                    dbc.Card(
                        dbc.CardBody(
                            [
                                dbc.Row(
                                    [
                                        dbc.Col(
                                            dcc.Dropdown(
                                                id="database-dropdown",
                                                options=get_databases(
                                                    _config.directory
                                                ),
                                                placeholder="Select a database...",
                                            ),
                                            width=6,
                                        ),
                                        dbc.Col(
                                            dcc.Dropdown(
                                                id="x-dropdown", placeholder="x-axis"
                                            ),
                                            width=2,
                                        ),
                                        dbc.Col(
                                            dcc.Dropdown(
                                                id="y-dropdown", placeholder="y-axis"
                                            ),
                                            width=2,
                                        ),
                                        dbc.Col(
                                            dcc.Input(
                                                id="jitter-input",
                                                type="number",
                                                value=0,
                                                placeholder="Jitter",
                                                min=0,
                                            ),
                                            width=2,
                                        ),
                                    ]
                                )
                            ]
                        ),
                        className="mb-3",
                    ),
                    dbc.Card(
                        dbc.CardBody(
                            [
                                html.Div(
                                    [
                                        dbc.Switch(
                                            id="toggle-auto-refresh",
                                            label="Auto-Refresh",
                                            value=_config.refresh_interval > 0,
                                            style={"margin-right": "20px"},
                                        ),
                                        dcc.Input(
                                            id="refresh-interval-input",
                                            type="number",
                                            placeholder="Seconds",
                                            min=1,
                                            step=1,
                                            value=_config.refresh_interval or None,
                                            style={"width": "100px"},
                                        ),
                                    ],
                                    style={"display": "flex", "alignItems": "center"},
                                )
                            ]
                        ),
                        className="mb-3",
                    ),
                    dbc.Card(
                        dbc.CardBody(
                            [
                                dcc.Graph(id="graph", style={"height": "60vh"}),
                                html.Hr(),
                                dbc.Switch(
                                    id="switch-fixgreen",
                                    value=True,
                                    label="Fix Green (don't recolor successful glitches)",
                                ),
                                html.Div(
                                    [
                                        dcc.Input(
                                            id=f"recolor-{c}",
                                            type="text",
                                            placeholder=f"Regex for {c.capitalize()}",
                                            style={
                                                "width": "150px",
                                                "margin-right": "5px",
                                            },
                                            persistence=True,
                                        )
                                        for c in _COLORS
                                    ]
                                ),
                                html.Div(
                                    [
                                        dcc.Input(
                                            id=f"recolor-{c}-label",
                                            type="text",
                                            placeholder=f"{c.capitalize()} Label",
                                            style={
                                                "width": "150px",
                                                "margin-right": "5px",
                                            },
                                            persistence=True,
                                        )
                                        for c in _COLORS
                                    ],
                                    style={"margin-top": "10px"},
                                ),
                            ]
                        ),
                        className="mb-3",
                    ),
                    dbc.Card(
                        dbc.CardBody(
                            [
                                html.Div(
                                    [
                                        dbc.Switch(
                                            id="switch-squeezedata",
                                            value=True,
                                            label="Squeeze Data",
                                            style={"margin-right": "20px"},
                                        ),
                                        dbc.Switch(
                                            id="switch-showhexdata",
                                            value=False,
                                            label="Show Hex",
                                            style={"margin-right": "20px"},
                                        ),
                                        dbc.Switch(
                                            id="switch-wraptext",
                                            value=False,
                                            label="Wrap Text",
                                            style={"margin-right": "20px"},
                                        ),
                                        dcc.Input(
                                            id="response_s",
                                            type="number",
                                            placeholder="slice start",
                                            style={
                                                "width": "100px",
                                                "margin-right": "10px",
                                            },
                                            persistence=True,
                                        ),
                                        dcc.Input(
                                            id="response_e",
                                            type="number",
                                            placeholder="slice end",
                                            style={"width": "100px"},
                                            persistence=True,
                                        ),
                                    ],
                                    className="mb-3",
                                ),
                                html.Div(
                                    id="data", style={"width": "100%", "height": "100%"}
                                ),
                            ]
                        )
                    ),
                    dbc.Card(
                        dbc.CardBody(
                            [
                                dbc.CardHeader("Arguments:"),
                                dcc.Markdown("N/A", id="argv"),
                            ]
                        ),
                        className="mt-3",
                    ),
                ],
                style={"width": "90%", "margin": "0 auto"},
            ),
        ],
        style={"width": "100%", "margin-top": "50px", "margin-bottom": "100px"},
    )


#
# Main Execution
#
def check_env() -> NoReturn:
    required = ["ANALYZER_DIRECTORY"]
    if missing := [var for var in required if var not in os.environ]:
        raise ValueError(f"Missing required environment variables: {missing}")


if __name__ == "__main__":
    __version__ = "2.2.1"  # Bugfix version
    parser = argparse.ArgumentParser(
        description=f"analyzer.py v{__version__} - Raelize Glitch Analyzer",
        prog="analyzer",
    )
    parser.add_argument("--ip", help="Server IP", type=str, default="127.0.0.1")
    parser.add_argument("--port", help="Server port", type=int, default=8000)
    parser.add_argument("--directory", help="Database directory", required=True)
    parser.add_argument("--x", required=False, help="Preset the x parameter")
    parser.add_argument("--y", required=False, help="Preset the y parameter")
    parser.add_argument(
        "--refresh-interval",
        type=int,
        default=0,
        help="Auto-refresh interval in seconds (0 to disable).",
    )
    args = parser.parse_args()
    (
        _config.serverip,
        _config.serverport,
        _config.directory,
        _config.x,
        _config.y,
        _config.refresh_interval,
    ) = args.ip, args.port, args.directory, args.x, args.y, args.refresh_interval
    app = Dash(__name__, external_stylesheets=[dbc.themes.JOURNAL])
    server = app.server
    register_callbacks(app)
    create_layout(app)
    app.run(host=_config.serverip, port=_config.serverport, debug=True)
