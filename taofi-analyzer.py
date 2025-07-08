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
    directory: str = None
    database: str = None
    y: str = None
    x: str = None
    z: str = None
    jitter: int = 0
    argv: str = None
    query: str = ""
    colors: Dict[str, list] = field(default_factory=dict)
    refresh_interval: int = 0
    plot_type: str = "scatter"
    theme: str = "light"


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


def get_number_of_experiments(d: str, db: str) -> int:
    try:
        with query_db(os.path.join(d, db)) as c:
            return c.cursor().execute("SELECT COUNT(*) FROM experiments").fetchone()[0]
    except:
        return 0


def get_databases(d: str) -> List[Dict[str, str]]:
    if not d or not os.path.isdir(d):
        return []
    return [
        {"label": f"{db} ({get_number_of_experiments(d, db)})", "value": db}
        for db in sorted([f for f in listdir(d) if f.endswith(".sqlite")], reverse=True)
    ]


def get_db_metadata(d: str, db: str, c: str) -> Optional[str]:
    try:
        with query_db(os.path.join(d, db)) as con:
            return con.cursor().execute(f"SELECT {c} FROM metadata").fetchone()[0]
    except Exception as e:
        print(f"ERROR (get_db_metadata for {c}): {e}", file=sys.stderr)
        return None


def get_parameters(d: str, db: str) -> List[str]:
    try:
        with query_db(os.path.join(d, db)) as c:
            params = [
                desc[0]
                for desc in c.cursor()
                .execute("SELECT * FROM experiments LIMIT 1")
                .description
            ]
            if "response" in params:
                params.remove("response")
            return params
    except Exception as e:
        print(f"ERROR (get_parameters): {e}", file=sys.stderr)
        return []


def get_variable_names(cols: List[str]) -> Dict[str, Optional[str]]:
    m = {
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
    return {k: next((n for n in p if n in cols), None) for k, p in m.items()}


def recolor_row(r: pd.Series, rgx: Optional[bytes], nc: str, fg: bool) -> str:
    if not rgx or (fg and r["color"] == "G"):
        return r["color"]
    if r["response_bytes"] and re.search(rgx, r["response_bytes"]):
        return nc
    return r["color"]


def generate_data_table(
    df: pd.DataFrame, sq: bool, sh: bool, s: Optional[int], e: Optional[int]
) -> List[Dict[str, Any]]:
    if df.empty:
        return []
    v = get_variable_names(df.columns)
    df_p = df.copy()
    df_p["response_bytes"] = df_p[v["response"]].apply(
        lambda s: base64.b64decode(s) if isinstance(s, str) else b""
    )
    df_p["response_sliced"] = df_p["response_bytes"].str.slice(s, e)
    df_p["response_str"] = df_p["response_sliced"].apply(
        lambda x: x.decode("utf-8", errors="replace")
    )
    if sh:
        df_p["hex(response)"] = df_p["response_sliced"].apply(
            lambda x: x.hex(" ") if x else ""
        )
    if not sq:
        k = ["id", "color", "delay", "response_str"]
        for p in ["normal", "length", "power", "voltage", "reset"]:
            if v[p]:
                k.append(v[p])
        if sh:
            k.append("hex(response)")
        df_p.rename(columns={"response_str": "response"}, inplace=True)
        return df_p[[c for c in k if c in df_p.columns]].to_dict("records")
    else:
        p_map = {
            "Delay": v["delay"],
            "Normal": v["normal"],
            "Length": v["length"],
            "Power": v["power"],
            "Voltage": v["voltage"],
            "Reset": v["reset"],
        }
        squeezed = (
            df_p.groupby(["response_str", "color"])
            .agg(
                amount=("id", "count"),
                **{
                    f"Min({n})": (c, "min")
                    for n, c in p_map.items()
                    if c and c in df_p.columns
                },
                **{
                    f"Max({n})": (c, "max")
                    for n, c in p_map.items()
                    if c and c in df_p.columns
                },
            )
            .reset_index()
        )
        if sh:
            squeezed = squeezed.merge(
                df_p.drop_duplicates(subset=["response_str"])[
                    ["response_str", "hex(response)"]
                ],
                on="response_str",
            )
        squeezed.rename(columns={"response_str": "response"}, inplace=True)
        return squeezed.sort_values(by="amount", ascending=False).to_dict("records")


def give_xy_label(p: str) -> str:
    l = {
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
    return l.get(p, "")


#
# Callbacks
#
def register_callbacks(app):
    @app.callback(
        Output("config-store", "data"),
        [
            Input("database-dropdown", "value"),
            Input("x-dropdown", "value"),
            Input("y-dropdown", "value"),
            Input("z-dropdown", "value"),
            Input("query-input", "value"),
            Input("jitter-input", "value"),
            Input("plot-type-radio", "value"),
        ]
        + [Input(f"recolor-{c}", "value") for c in _COLORS]
        + [Input(f"recolor-{c}-label", "value") for c in _COLORS],
        State("config-store", "data"),
    )
    def update_config_store(database, x, y, z, query, jitter, plot_type, *states):
        store, color_states = states[-1], states[:-1]
        config = AnalyzerConfig(**store)
        if not ctx.triggered_id:
            raise PreventUpdate

        (
            config.database,
            config.x,
            config.y,
            config.z,
            config.query,
            config.jitter,
            config.plot_type,
        ) = database, x, y, z, query, jitter, plot_type
        for i, color in enumerate(_COLORS):
            config.colors[color] = [color_states[i], color_states[i + len(_COLORS)]]
        if ctx.triggered_id == "database-dropdown" and database:
            config.argv = get_db_metadata(config.directory, database, "argv")
        return asdict(config)

    @app.callback(Output("z-axis-col", "style"), Input("plot-type-radio", "value"))
    def toggle_z_axis_visibility(plot_type):
        return (
            {"display": "block"} if plot_type == "scatter_3d" else {"display": "none"}
        )

    # Fixed: Auto-load data when database is selected
    @app.callback(
        Output("records-store", "data"),
        [
            Input("update-button", "n_clicks"),
            Input("auto-refresh-interval", "n_intervals"),
            Input("database-dropdown", "value"),  # This triggers auto-load
        ],
        State("config-store", "data"),
        prevent_initial_call=False,
    )
    def update_records_store(n_clicks, n_intervals, database_value, store):
        config = AnalyzerConfig(**store)

        # If database dropdown triggered this and we have a database, load data
        if ctx.triggered_id == "database-dropdown":
            if not database_value or not config.directory:
                return []
            # Update config with new database for this load
            config.database = database_value

        if not all([config.directory, config.database]):
            return []

        db_path = os.path.join(config.directory, config.database)
        if not os.path.isfile(db_path):
            return []

        query = "SELECT * FROM experiments" + (
            f" WHERE {config.query}" if config.query else ""
        )
        try:
            with query_db(db_path) as conn:
                df = pd.read_sql_query(query, conn)
        except Exception as e:
            print(f"ERROR: Query failed. {e}", file=sys.stderr)
            return []

        if df.empty:
            return []

        if config.jitter > 0:
            for axis in [config.x, config.y]:
                if axis in df.columns and pd.api.types.is_numeric_dtype(df[axis]):
                    df[axis] += np.random.normal(0, config.jitter, df.shape[0])

        if "response" in df.columns:
            df["response"] = df["response"].apply(
                lambda b: base64.b64encode(b).decode("ascii")
                if isinstance(b, bytes)
                else None
            )
        return df.to_dict("records")

    @app.callback(
        Output("graph", "figure"),
        [
            Input("records-store", "data"),
            Input("plot-type-radio", "value"),
            Input("x-dropdown", "value"),
            Input("y-dropdown", "value"),
            Input("z-dropdown", "value"),
            Input("switch-fixgreen", "value"),
        ],
        State("config-store", "data"),
        prevent_initial_call=True,
    )
    def update_graph(records_data, plot_type, x, y, z, fixgreen, store):
        if not records_data:
            return px.scatter(title="No data to display")
        df = pd.DataFrame.from_records(records_data)
        config = AnalyzerConfig(**store)
        plotly_template = "plotly"  # Use default plotly theme

        if "response" in df.columns:
            df["response_bytes"] = df["response"].apply(
                lambda s: base64.b64decode(s) if isinstance(s, str) else b""
            )
        df["color_new"] = df["color"]
        for color_name, (regex, _) in config.colors.items():
            if regex:
                df["color_new"] = df.apply(
                    recolor_row,
                    axis=1,
                    args=(
                        re.compile(regex.encode(errors="strict")),
                        _COLOR_MAP_CODES[color_name],
                        fixgreen,
                    ),
                )

        color_counts, total_records = df["color_new"].value_counts().to_dict(), len(df)
        common_args = dict(
            color="color_new",
            template=plotly_template,
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

        fig = None
        if plot_type == "scatter":
            if not all([x, y, x in df.columns, y in df.columns]):
                raise PreventUpdate
            fig = px.scatter(
                df,
                x=x,
                y=y,
                render_mode="webgl",
                **common_args,
                labels={x: f"{x} {give_xy_label(x)}", y: f"{y} {give_xy_label(y)}"},
            )

        elif plot_type == "scatter_3d":
            if not all([x, y, z, x in df.columns, y in df.columns, z in df.columns]):
                raise PreventUpdate
            fig = px.scatter_3d(
                df,
                x=x,
                y=y,
                z=z,
                **common_args,
                labels={
                    x: f"{x} {give_xy_label(x)}",
                    y: f"{y} {give_xy_label(y)}",
                    z: f"{z} {give_xy_label(z)}",
                },
            )

        elif plot_type == "density_heatmap":
            if not all([x, y, x in df.columns, y in df.columns]):
                raise PreventUpdate
            # For density heatmap, we need numeric data
            if not (
                pd.api.types.is_numeric_dtype(df[x])
                and pd.api.types.is_numeric_dtype(df[y])
            ):
                # If data is not numeric, create a fallback 2D histogram
                fig = px.density_heatmap(
                    df,
                    x=x,
                    y=y,
                    template=plotly_template,
                    labels={x: f"{x} {give_xy_label(x)}", y: f"{y} {give_xy_label(y)}"},
                    title="Density Heatmap (Note: Non-numeric data may not display optimally)",
                )
            else:
                fig = px.density_heatmap(
                    df,
                    x=x,
                    y=y,
                    marginal_x="histogram",
                    marginal_y="histogram",
                    template=plotly_template,
                    labels={x: f"{x} {give_xy_label(x)}", y: f"{y} {give_xy_label(y)}"},
                )

        if not fig:
            raise PreventUpdate

        fig.update_layout(
            title_text="",
            uirevision=config.database,
            legend_title_text=f"Classification ({total_records:,})",
        )

        # Only add legend labels for scatter plots (not heatmap)
        if plot_type != "density_heatmap":
            legend_labels = {}
            for color_name, (regex, label) in config.colors.items():
                code, count = (
                    _COLOR_MAP_CODES[color_name],
                    color_counts.get(_COLOR_MAP_CODES[color_name], 0),
                )
                legend_labels[code] = (
                    f"{(regex or color_name.capitalize()) if not label else label} ({count:,} / {count / total_records:.1%})"
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

        column_defs = [{"field": c, "autoSize": True} for c in data[0].keys()]
        for c in column_defs:
            if c["field"] in ["id", "color", "amount"]:
                c.update({"maxWidth": 120, "pinned": "left"})
            elif "Min(" in c["field"] or "Max(" in c["field"]:
                c.update({"maxWidth": 150, "pinned": "left"})
            elif c["field"] == "response":
                c.update({"width": 500, "wrapText": wraptext, "autoHeight": wraptext})
            elif c["field"] == "hex(response)":
                c.update({"width": 500, "hide": not showhex})

        row_styles = {
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
                {
                    "condition": "params.data.color == 'P'",
                    "style": {"backgroundColor": "#f5e1e8"},
                },
            ]
        }
        return AgGrid(
            columnDefs=column_defs,
            rowData=data,
            defaultColDef={"resizable": True, "sortable": True, "filter": True},
            className="ag-theme-quartz",
            getRowStyle=row_styles,
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

    @app.callback(
        Output("auto-refresh-interval", "disabled"),
        Output("auto-refresh-interval", "interval"),
        Input("toggle-auto-refresh", "value"),
        Input("refresh-interval-input", "value"),
    )
    def manage_auto_refresh(is_on, secs):
        dis, ms = not is_on, (secs or 0) * 1000
        (dis := True) if ms <= 0 else None
        return dis, ms

    @app.callback(
        Output("database-dropdown", "options"),
        [
            Input("update-button", "n_clicks"),
            Input("auto-refresh-interval", "n_intervals"),
        ],
    )
    def update_database_list(c, i):
        return get_databases(_config.directory)

    @app.callback(
        Output("x-dropdown", "options"),
        Output("y-dropdown", "options"),
        Output("z-dropdown", "options"),
        Input("database-dropdown", "value"),
        State("config-store", "data"),
    )
    def update_axis_options(db, s):
        if not db:
            raise PreventUpdate
        p = get_parameters(s["directory"], db)
        return p, p, p

    @app.callback(Output("argv", "children"), Input("config-store", "data"))
    def update_argv(s):
        return s.get("argv", "N/A")


#
# Layout
#
def create_layout(app):
    navbar = dbc.Navbar(
        dbc.Container(
            [
                dbc.NavbarBrand(
                    [html.I(className="bi bi-magic me-2"), "Raelize Glitch Analyzer"]
                ),
            ]
        ),
        color="dark",
        dark=True,
        className="mb-4",
    )

    control_tabs = dbc.Tabs(
        [
            dbc.Tab(
                label="1. Data Source",
                children=[
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
                                            width=12,
                                        )
                                    ],
                                    className="mb-3",
                                ),
                                dbc.Row(
                                    [
                                        dbc.Col(
                                            html.Button(
                                                [
                                                    html.I(
                                                        className="bi bi-play-fill me-1"
                                                    ),
                                                    "Update Data",
                                                ],
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
                                            width=6,
                                        ),
                                        dbc.Col(
                                            dbc.Switch(
                                                id="toggle-auto-refresh",
                                                label="Auto-Refresh",
                                                value=_config.refresh_interval > 0,
                                            ),
                                            width={"size": 2, "offset": 0},
                                            className="d-flex align-items-center justify-content-center",
                                        ),
                                        dbc.Col(
                                            dcc.Input(
                                                id="refresh-interval-input",
                                                type="number",
                                                placeholder="sec",
                                                min=1,
                                                step=1,
                                                value=_config.refresh_interval or None,
                                                className="w-100",
                                            ),
                                            width=2,
                                        ),
                                    ],
                                    className="align-items-center",
                                ),
                            ]
                        )
                    )
                ],
            ),
            dbc.Tab(
                label="2. Plot Config",
                children=[
                    dbc.Card(
                        dbc.CardBody(
                            [
                                dbc.Row(
                                    [
                                        dbc.Col(
                                            [
                                                html.Label("Plot Type"),
                                                dcc.RadioItems(
                                                    id="plot-type-radio",
                                                    options=[
                                                        {
                                                            "label": "Scatter 2D",
                                                            "value": "scatter",
                                                        },
                                                        {
                                                            "label": "Scatter 3D",
                                                            "value": "scatter_3d",
                                                        },
                                                        {
                                                            "label": "Density Heatmap",
                                                            "value": "density_heatmap",
                                                        },
                                                    ],
                                                    value="scatter",
                                                    inline=True,
                                                    labelStyle={"margin-right": "15px"},
                                                    inputStyle={"margin-right": "5px"},
                                                ),
                                            ],
                                            width=12,
                                        )
                                    ],
                                    className="mb-3",
                                ),
                                dbc.Row(
                                    [
                                        dbc.Col(
                                            dcc.Dropdown(
                                                id="x-dropdown", placeholder="X-axis"
                                            ),
                                            width=3,
                                        ),
                                        dbc.Col(
                                            dcc.Dropdown(
                                                id="y-dropdown", placeholder="Y-axis"
                                            ),
                                            width=3,
                                        ),
                                        dbc.Col(
                                            dcc.Dropdown(
                                                id="z-dropdown", placeholder="Z-axis"
                                            ),
                                            width=3,
                                            id="z-axis-col",
                                        ),
                                        dbc.Col(
                                            [
                                                html.Label(
                                                    "Jitter", style={"display": "block"}
                                                ),
                                                dcc.Input(
                                                    id="jitter-input",
                                                    type="number",
                                                    value=0,
                                                    placeholder="Jitter",
                                                    min=0,
                                                    className="w-100",
                                                ),
                                            ],
                                            width=3,
                                        ),
                                    ]
                                ),
                            ]
                        )
                    )
                ],
            ),
            dbc.Tab(
                label="3. Recolor Rules",
                children=[
                    dbc.Card(
                        dbc.CardBody(
                            [
                                dbc.Switch(
                                    id="switch-fixgreen",
                                    value=True,
                                    label="Fix Green (don't recolor successful glitches)",
                                    className="mb-3",
                                ),
                                html.Div(
                                    [
                                        dcc.Input(
                                            id=f"recolor-{c}",
                                            type="text",
                                            placeholder=f"Regex for {c.capitalize()}",
                                            style={
                                                "width": "11%",
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
                                                "width": "11%",
                                                "margin-right": "5px",
                                            },
                                            persistence=True,
                                        )
                                        for c in _COLORS
                                    ],
                                    style={"margin-top": "10px"},
                                ),
                            ]
                        )
                    )
                ],
            ),
        ]
    )

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
            html.Div(id="theme-dummy", style={"display": "none"}),
            navbar,
            dbc.Container(
                [
                    control_tabs,
                    dbc.Card(
                        dbc.CardBody(
                            [
                                dcc.Loading(
                                    id="loading-graph",
                                    type="default",
                                    children=dcc.Graph(
                                        id="graph", style={"height": "70vh"}
                                    ),
                                )
                            ]
                        ),
                        className="my-4",
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
                                    className="mb-3 d-flex align-items-center",
                                ),
                                dcc.Loading(
                                    id="loading-data-table",
                                    type="default",
                                    children=html.Div(
                                        id="data",
                                        style={"width": "100%", "height": "100%"},
                                    ),
                                ),
                            ]
                        )
                    ),
                    dbc.Card(
                        dbc.CardBody(
                            [
                                dbc.CardHeader("Command-line Arguments:"),
                                dcc.Markdown("N/A", id="argv"),
                            ]
                        ),
                        className="mt-4",
                    ),
                ],
                fluid=True,
            ),
        ]
    )


#
# Main Execution
#
def check_env() -> NoReturn:
    if missing := [var for var in ["ANALYZER_DIRECTORY"] if var not in os.environ]:
        raise ValueError(f"Missing required environment variables: {missing}")


if __name__ == "__main__":
    __version__ = "3.2"
    parser = argparse.ArgumentParser(
        description=f"analyzer.py v{__version__} - Raelize Glitch Analyzer",
        prog="analyzer",
    )
    parser.add_argument("--ip", type=str, default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("directory", nargs=1, type=str, help="Directory with databases")
    parser.add_argument("--x", required=False)
    parser.add_argument("--y", required=False)
    parser.add_argument("--refresh-interval", type=int, default=0)
    args = parser.parse_args()
    (
        _config.serverip,
        _config.serverport,
        _config.directory,
        _config.x,
        _config.y,
        _config.refresh_interval,
    ) = args.ip, args.port, args.directory[0], args.x, args.y, args.refresh_interval

    app = Dash(
        __name__,
        external_stylesheets=[dbc.themes.JOURNAL, dbc.icons.BOOTSTRAP],
    )
    server = app.server
    register_callbacks(app)
    create_layout(app)
    app.run(host=_config.serverip, port=_config.serverport, debug=True)
