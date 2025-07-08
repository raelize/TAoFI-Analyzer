#!/usr/bin/env python3
"""
Raelize Glitch Analyzer v2.1
A Dash-based web application for analyzing glitch experiment data.
"""

# ============================================================================
# IMPORTS
# ============================================================================

import argparse
import base64
import os
import re
import sqlite3
import sys
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from os import listdir
from typing import Any, Dict, List, Optional

import dash_bootstrap_components as dbc
import numpy as np
import pandas as pd
import plotly.express as px
from dash import Dash, Input, Output, State, dcc, html
from dash import callback_context as ctx
from dash.exceptions import PreventUpdate
from dash_ag_grid import AgGrid
from dataclasses_json import dataclass_json

# ============================================================================
# CONFIGURATION & DATA MODELS
# ============================================================================


@dataclass_json
@dataclass
class AnalyzerConfig:
    """Configuration dataclass for the analyzer application."""

    serverip: str = "127.0.0.1"
    serverport: int = 8080
    directory: str = ""
    database: str = ""
    y: str = ""
    x: str = ""
    z: str = ""
    jitter: int = 0
    argv: str = ""
    query: str = ""
    colors: Dict[str, list] = field(default_factory=dict)
    refresh_interval: int = 0
    plot_type: str = "scatter"
    theme: str = "JOURNAL"


# ============================================================================
# THEME CONFIGURATION
# ============================================================================

# Theme utilities (based on dash-bootstrap-templates)
dbc_themes_url = {
    item: getattr(dbc.themes, item)
    for item in dir(dbc.themes)
    if not item.startswith(("_", "GRID"))
}

AVAILABLE_THEMES = list(dbc_themes_url.keys())
dbc_dark_themes = ["CYBORG", "DARKLY", "SLATE", "SOLAR", "SUPERHERO", "VAPOR"]


# ============================================================================
# GLOBAL CONSTANTS & VARIABLES
# ============================================================================

_config = AnalyzerConfig()

# Color mapping for experiment results
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

# Initialize color configuration
for color in _COLORS:
    _config.colors[color] = [None, None]


# ============================================================================
# DATABASE UTILITIES
# ============================================================================


@contextmanager
def query_db(db_path: str):
    """Context manager for database connections with custom functions."""
    conn = None
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
        if conn:
            conn.close()


def get_number_of_experiments(directory: str, database: str) -> int:
    """Get the number of experiments in a database."""
    try:
        with query_db(os.path.join(directory, database)) as conn:
            return (
                conn.cursor().execute("SELECT COUNT(*) FROM experiments").fetchone()[0]
            )
    except:
        return 0


def get_databases(directory: str) -> List[Dict[str, str]]:
    """Get list of available databases in directory."""
    if not directory or not os.path.isdir(directory):
        return []
    return [
        {"label": f"{db} ({get_number_of_experiments(directory, db)})", "value": db}
        for db in sorted(
            [f for f in listdir(directory) if f.endswith(".sqlite")], reverse=True
        )
    ]


def get_db_metadata(directory: str, database: str, column: str) -> Optional[str]:
    """Get metadata from database."""
    try:
        with query_db(os.path.join(directory, database)) as conn:
            return conn.cursor().execute(f"SELECT {column} FROM metadata").fetchone()[0]
    except Exception as e:
        print(f"ERROR (get_db_metadata for {column}): {e}", file=sys.stderr)
        return None


def get_parameters(directory: str, database: str) -> List[str]:
    """Get column parameters from experiments table."""
    try:
        with query_db(os.path.join(directory, database)) as conn:
            params = [
                desc[0]
                for desc in conn.cursor()
                .execute("SELECT * FROM experiments LIMIT 1")
                .description
            ]
            if "response" in params:
                params.remove("response")
            return params
    except Exception as e:
        print(f"ERROR (get_parameters): {e}", file=sys.stderr)
        return []


# ============================================================================
# DATA PROCESSING UTILITIES
# ============================================================================


def get_variable_names(columns: List[str]) -> Dict[str, Optional[str]]:
    """Map generic variable names to actual column names."""
    mapping = {
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
    return {
        k: next((name for name in possibilities if name in columns), None)
        for k, possibilities in mapping.items()
    }


def recolor_row(
    row: pd.Series, regex: Optional[bytes], new_color: str, fix_green: bool
) -> str:
    """Apply recoloring rules to experiment rows."""
    if not regex or (fix_green and row["color"] == "G"):
        return row["color"]
    if row["response_bytes"] and re.search(regex, row["response_bytes"]):
        return new_color
    return row["color"]


def generate_data_table(
    df: pd.DataFrame,
    squeeze: bool,
    show_hex: bool,
    slice_start: Optional[int],
    slice_end: Optional[int],
) -> List[Dict[str, Any]]:
    """Generate data table from DataFrame with various formatting options."""
    if df.empty:
        return []

    var_names = get_variable_names(df.columns)
    df_processed = df.copy()

    # Process response data
    df_processed["response_bytes"] = df_processed[var_names["response"]].apply(
        lambda s: base64.b64decode(s) if isinstance(s, str) else b""
    )
    df_processed["response_sliced"] = df_processed["response_bytes"].str.slice(
        slice_start, slice_end
    )
    df_processed["response_str"] = df_processed["response_sliced"].apply(
        lambda x: x.decode("utf-8", errors="replace")
    )

    if show_hex:
        df_processed["hex(response)"] = df_processed["response_sliced"].apply(
            lambda x: x.hex(" ") if x else ""
        )

    if not squeeze:
        # Standard table format
        columns = ["id", "color", "delay", "response_str"]
        for param in ["normal", "length", "power", "voltage", "reset"]:
            if var_names[param]:
                columns.append(var_names[param])
        if show_hex:
            columns.append("hex(response)")

        df_processed.rename(columns={"response_str": "response"}, inplace=True)
        return df_processed[[c for c in columns if c in df_processed.columns]].to_dict(
            "records"
        )

    else:
        # Squeezed/grouped table format
        param_mapping = {
            "Delay": var_names["delay"],
            "Normal": var_names["normal"],
            "Length": var_names["length"],
            "Power": var_names["power"],
            "Voltage": var_names["voltage"],
            "Reset": var_names["reset"],
        }

        aggregation_dict = {"amount": ("id", "count")}
        aggregation_dict.update(
            {
                f"Min({name})": (col, "min")
                for name, col in param_mapping.items()
                if col and col in df_processed.columns
            }
        )
        aggregation_dict.update(
            {
                f"Max({name})": (col, "max")
                for name, col in param_mapping.items()
                if col and col in df_processed.columns
            }
        )

        squeezed = (
            df_processed.groupby(["response_str", "color"])
            .agg(**aggregation_dict)
            .reset_index()
        )

        if show_hex:
            squeezed = squeezed.merge(
                df_processed.drop_duplicates(subset=["response_str"])[
                    ["response_str", "hex(response)"]
                ],
                on="response_str",
            )

        squeezed.rename(columns={"response_str": "response"}, inplace=True)
        return squeezed.sort_values(by="amount", ascending=False).to_dict("records")


def give_xy_label(parameter: str) -> str:
    """Get appropriate unit label for parameters."""
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


# ============================================================================
# DASH CALLBACKS
# ============================================================================


def register_callbacks(app):
    """Register all Dash callbacks."""

    # Theme switching callback
    app.clientside_callback(
        """
        function (selected_theme, themes) {
            if (!selected_theme) return window.dash_clientside.no_update;

            // Find existing theme stylesheets
            let stylesheets = []
            Object.values(themes).forEach(
                url => stylesheets.push(...document.querySelectorAll(`link[rel='stylesheet'][href*='${url}']`))
            );

            // Create a new stylesheet link element
            let newStylesheet = document.createElement("link");
            newStylesheet.rel = "stylesheet";
            newStylesheet.href = selected_theme;

            // When the new stylesheet is loaded, remove the old ones
            newStylesheet.onload = function () {
                stylesheets.forEach(s => s.remove());
            }

            // Append the new stylesheet to the document head
            document.head.appendChild(newStylesheet);

            return window.dash_clientside.no_update;
        }
        """,
        Output("theme-dummy", "children"),
        Input("theme-dropdown", "value"),
        State("theme-store", "data"),
    )

    # Configuration store callback
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
            Input("theme-dropdown", "value"),
        ]
        + [Input(f"recolor-{c}", "value") for c in _COLORS]
        + [Input(f"recolor-{c}-label", "value") for c in _COLORS],
        State("config-store", "data"),
    )
    def update_config_store(
        database, x, y, z, query, jitter, plot_type, theme_url, *states
    ):
        """Update the configuration store when any input changes."""
        store, color_states = states[-1], states[:-1]
        config = AnalyzerConfig(**store)

        if not ctx.triggered_id:
            raise PreventUpdate

        # Convert theme URL back to theme name
        url_to_theme = {v: k for k, v in dbc_themes_url.items()}
        theme_name = url_to_theme.get(theme_url, "JOURNAL")

        # Update configuration
        config.database = database
        config.x = x
        config.y = y
        config.z = z
        config.query = query
        config.jitter = jitter
        config.plot_type = plot_type
        config.theme = theme_name

        # Update color configurations
        for i, color in enumerate(_COLORS):
            config.colors[color] = [color_states[i], color_states[i + len(_COLORS)]]

        # Update argv if database changed
        if ctx.triggered_id == "database-dropdown" and database:
            config.argv = get_db_metadata(config.directory, database, "argv")

        return asdict(config)

    # Z-axis visibility toggle
    @app.callback(Output("z-axis-col", "style"), Input("plot-type-radio", "value"))
    def toggle_z_axis_visibility(plot_type):
        """Show/hide Z-axis dropdown based on plot type."""
        return (
            {"display": "block"} if plot_type == "scatter_3d" else {"display": "none"}
        )

    # Data loading callback
    @app.callback(
        Output("records-store", "data"),
        [
            Input("update-button", "n_clicks"),
            Input("auto-refresh-interval", "n_intervals"),
            Input("database-dropdown", "value"),
        ],
        State("config-store", "data"),
        prevent_initial_call=False,
    )
    def update_records_store(n_clicks, n_intervals, database_value, store):
        """Load experiment data from database."""
        config = AnalyzerConfig(**store)

        # Auto-load data when database is selected
        if ctx.triggered_id == "database-dropdown":
            if not database_value or not config.directory:
                return []
            config.database = database_value

        if not all([config.directory, config.database]):
            return []

        db_path = os.path.join(config.directory, config.database)
        if not os.path.isfile(db_path):
            return []

        # Build SQL query
        query = "SELECT * FROM experiments"
        if config.query:
            query += f" WHERE {config.query}"

        try:
            with query_db(db_path) as conn:
                df = pd.read_sql_query(query, conn)
        except Exception as e:
            print(f"ERROR: Query failed. {e}", file=sys.stderr)
            return []

        if df.empty:
            return []

        # Apply jitter if specified
        if config.jitter > 0:
            for axis in [config.x, config.y]:
                if axis in df.columns and pd.api.types.is_numeric_dtype(df[axis]):
                    df[axis] += np.random.normal(0, config.jitter, df.shape[0])

        # Encode response data
        if "response" in df.columns:
            df["response"] = df["response"].apply(
                lambda b: base64.b64encode(b).decode("ascii")
                if isinstance(b, bytes)
                else None
            )

        return df.to_dict("records")

    # Graph plotting callback
    @app.callback(
        Output("graph", "figure"),
        [
            Input("records-store", "data"),
            Input("plot-type-radio", "value"),
            Input("x-dropdown", "value"),
            Input("y-dropdown", "value"),
            Input("z-dropdown", "value"),
            Input("switch-fixgreen", "value"),
            Input("theme-dropdown", "value"),
        ],
        State("config-store", "data"),
        prevent_initial_call=True,
    )
    def update_graph(records_data, plot_type, x, y, z, fixgreen, theme_url, store):
        """Generate plotly graph based on current settings."""
        if not records_data:
            return px.scatter(title="No data to display")

        df = pd.DataFrame.from_records(records_data)
        config = AnalyzerConfig(**store)

        # Determine plotly template based on theme
        url_to_theme = {v: k for k, v in dbc_themes_url.items()}
        current_theme = url_to_theme.get(theme_url, "JOURNAL")
        plotly_template = (
            "plotly_dark" if current_theme in dbc_dark_themes else "plotly"
        )

        # Process response data for recoloring
        if "response" in df.columns:
            df["response_bytes"] = df["response"].apply(
                lambda s: base64.b64decode(s) if isinstance(s, str) else b""
            )

        # Apply recoloring rules
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

        # Prepare plot arguments
        color_counts = df["color_new"].value_counts().to_dict()
        total_records = len(df)

        common_args = {
            "color": "color_new",
            "template": plotly_template,
            "color_discrete_map": {
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
            "category_orders": {
                "color_new": ["P", "G", "Y", "M", "O", "C", "B", "Z", "R"]
            },
        }

        # Generate appropriate plot
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

            if not (
                pd.api.types.is_numeric_dtype(df[x])
                and pd.api.types.is_numeric_dtype(df[y])
            ):
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

        # Update layout
        fig.update_layout(
            title_text="",
            uirevision=config.database,
            legend_title_text=f"Classification ({total_records:,})",
        )

        # Add custom legend labels for scatter plots
        if plot_type != "density_heatmap":
            legend_labels = {}
            for color_name, (regex, label) in config.colors.items():
                code = _COLOR_MAP_CODES[color_name]
                count = color_counts.get(code, 0)
                display_name = label if label else (regex or color_name.capitalize())
                legend_labels[code] = (
                    f"{display_name} ({count:,} / {count / total_records:.1%})"
                    if total_records
                    else f"{display_name} (0)"
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

    # Data table callback
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
    def update_data_table(
        records_data, squeeze, showhex, wraptext, slice_start, slice_end
    ):
        """Update the data table display."""
        if not records_data:
            return "No data available."

        df = pd.DataFrame.from_records(records_data)
        data = generate_data_table(df, squeeze, showhex, slice_start, slice_end)

        if not data:
            return "No results for current settings."

        # Configure column definitions
        column_defs = [{"field": c, "autoSize": True} for c in data[0].keys()]
        for col_def in column_defs:
            field = col_def["field"]
            if field in ["id", "color", "amount"]:
                col_def.update({"maxWidth": 120, "pinned": "left"})
            elif "Min(" in field or "Max(" in field:
                col_def.update({"maxWidth": 150, "pinned": "left"})
            elif field == "response":
                col_def.update(
                    {"width": 500, "wrapText": wraptext, "autoHeight": wraptext}
                )
            elif field == "hex(response)":
                col_def.update({"width": 500, "hide": not showhex})

        # Configure row styling
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

    # Auto-refresh management
    @app.callback(
        Output("auto-refresh-interval", "disabled"),
        Output("auto-refresh-interval", "interval"),
        Input("toggle-auto-refresh", "value"),
        Input("refresh-interval-input", "value"),
    )
    def manage_auto_refresh(is_on, seconds):
        """Manage auto-refresh interval settings."""
        disabled = not is_on
        milliseconds = (seconds or 0) * 1000
        if milliseconds <= 0:
            disabled = True
        return disabled, milliseconds

    # Database list updates
    @app.callback(
        Output("database-dropdown", "options"),
        [
            Input("update-button", "n_clicks"),
            Input("auto-refresh-interval", "n_intervals"),
        ],
    )
    def update_database_list(clicks, intervals):
        """Update the list of available databases."""
        return get_databases(_config.directory)

    # Axis options updates
    @app.callback(
        Output("x-dropdown", "options"),
        Output("y-dropdown", "options"),
        Output("z-dropdown", "options"),
        Input("database-dropdown", "value"),
        State("config-store", "data"),
    )
    def update_axis_options(database, store):
        """Update axis dropdown options based on selected database."""
        if not database:
            raise PreventUpdate
        parameters = get_parameters(store["directory"], database)
        return parameters, parameters, parameters

    # Command-line arguments display
    @app.callback(Output("argv", "children"), Input("config-store", "data"))
    def update_argv_display(store):
        """Display command-line arguments from database metadata."""
        return store.get("argv", "N/A")


# ============================================================================
# UI LAYOUT COMPONENTS
# ============================================================================


def create_navbar():
    """Create the application navbar."""
    return dbc.Navbar(
        dbc.Container(
            [
                dbc.NavbarBrand(
                    [html.I(className="bi bi-magic me-2"), "Raelize Glitch Analyzer"]
                ),
                dbc.Col(
                    [
                        html.Label("Theme:", className="text-light me-2"),
                        dcc.Dropdown(
                            id="theme-dropdown",
                            options=[
                                {"label": theme.title(), "value": dbc_themes_url[theme]}
                                for theme in AVAILABLE_THEMES
                            ],
                            value=dbc_themes_url[_config.theme],
                            clearable=False,
                            persistence=True,
                            persistence_type="local",
                            style={"width": "150px", "color": "black"},
                        ),
                    ],
                    width="auto",
                    className="d-flex align-items-center",
                ),
            ]
        ),
        color="dark",
        dark=True,
        className="mb-4",
    )


def create_plot_config_tab():
    """Create the plot configuration tab."""
    return dbc.Tab(
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
                                    [
                                        dcc.Dropdown(
                                            id="x-dropdown", placeholder="X-axis"
                                        )
                                    ],
                                    width=3,
                                ),
                                dbc.Col(
                                    [
                                        dcc.Dropdown(
                                            id="y-dropdown", placeholder="Y-axis"
                                        )
                                    ],
                                    width=3,
                                ),
                                dbc.Col(
                                    [
                                        dcc.Dropdown(
                                            id="z-dropdown", placeholder="Z-axis"
                                        )
                                    ],
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
    )


def create_data_source_tab():
    """Create the data source configuration tab."""
    return dbc.Tab(
        label="1. Data Source",
        children=[
            dbc.Card(
                dbc.CardBody(
                    [
                        dbc.Row(
                            [
                                dbc.Col(
                                    [
                                        dcc.Dropdown(
                                            id="database-dropdown",
                                            options=get_databases(_config.directory),
                                            placeholder="Select a database...",
                                        )
                                    ],
                                    width=12,
                                )
                            ],
                            className="mb-3",
                        ),
                        dbc.Row(
                            [
                                dbc.Col(
                                    [
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
                                        )
                                    ],
                                    width=2,
                                ),
                                dbc.Col(
                                    [
                                        dcc.Input(
                                            id="query-input",
                                            type="text",
                                            value="",
                                            className="w-100",
                                            placeholder="e.g., color = 'G' AND delay > 100",
                                            persistence=True,
                                        )
                                    ],
                                    width=6,
                                ),
                                dbc.Col(
                                    [
                                        dbc.Switch(
                                            id="toggle-auto-refresh",
                                            label="Auto-Refresh",
                                            value=_config.refresh_interval > 0,
                                        )
                                    ],
                                    width=2,
                                    className="d-flex align-items-center justify-content-center",
                                ),
                                dbc.Col(
                                    [
                                        dcc.Input(
                                            id="refresh-interval-input",
                                            type="number",
                                            placeholder="sec",
                                            min=1,
                                            step=1,
                                            value=_config.refresh_interval or None,
                                            className="w-100",
                                        )
                                    ],
                                    width=2,
                                ),
                            ],
                            className="align-items-center",
                        ),
                    ]
                )
            )
        ],
    )


def create_recolor_rules_tab():
    """Create the recolor rules configuration tab."""
    return dbc.Tab(
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
                                    id=f"recolor-{color}",
                                    type="text",
                                    placeholder=f"Regex for {color.capitalize()}",
                                    style={"width": "11%", "margin-right": "5px"},
                                    persistence=True,
                                )
                                for color in _COLORS
                            ]
                        ),
                        html.Div(
                            [
                                dcc.Input(
                                    id=f"recolor-{color}-label",
                                    type="text",
                                    placeholder=f"{color.capitalize()} Label",
                                    style={"width": "11%", "margin-right": "5px"},
                                    persistence=True,
                                )
                                for color in _COLORS
                            ],
                            style={"margin-top": "10px"},
                        ),
                    ]
                )
            )
        ],
    )


def create_control_tabs():
    """Create the main control tabs."""
    return dbc.Tabs(
        [
            create_data_source_tab(),
            create_plot_config_tab(),
            create_recolor_rules_tab(),
        ]
    )


def create_graph_card():
    """Create the main graph display card."""
    return dbc.Card(
        dbc.CardBody(
            [
                dcc.Loading(
                    id="loading-graph",
                    type="default",
                    children=dcc.Graph(id="graph", style={"height": "70vh"}),
                )
            ]
        ),
        className="my-4",
    )


def create_data_table_card():
    """Create the data table display card."""
    return dbc.Card(
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
                            style={"width": "100px", "margin-right": "10px"},
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
                        id="data", style={"width": "100%", "height": "100%"}
                    ),
                ),
            ]
        )
    )


def create_argv_card():
    """Create the command-line arguments display card."""
    return dbc.Card(
        dbc.CardBody(
            [
                dbc.CardHeader("Command-line Arguments:"),
                dcc.Markdown("N/A", id="argv"),
            ]
        ),
        className="mt-4",
    )


def create_layout(app):
    """Create the complete application layout."""
    app.layout = html.Div(
        id="app-container",
        children=[
            # Data stores
            dcc.Store(id="config-store", data=asdict(_config)),
            dcc.Store(id="records-store", data=[]),
            dcc.Store(id="theme-store", data=dbc_themes_url),
            # Auto-refresh interval
            dcc.Interval(
                id="auto-refresh-interval",
                interval=(_config.refresh_interval or 60) * 1000,
                n_intervals=0,
                disabled=_config.refresh_interval <= 0,
            ),
            # Theme dummy div for clientside callback
            html.Div(id="theme-dummy", style={"display": "none"}),
            # Main layout
            create_navbar(),
            dbc.Container(
                [
                    create_control_tabs(),
                    create_graph_card(),
                    create_data_table_card(),
                    create_argv_card(),
                ],
                fluid=True,
            ),
        ],
    )


# ============================================================================
# APPLICATION INITIALIZATION
# ============================================================================


def validate_environment():
    """Validate required environment variables."""
    missing_vars = [var for var in ["ANALYZER_DIRECTORY"] if var not in os.environ]
    if missing_vars:
        raise ValueError(f"Missing required environment variables: {missing_vars}")


def parse_arguments():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Raelize Glitch Analyzer v2.1 - A Dash-based web application for analyzing glitch experiment data",
        prog="analyzer",
    )
    parser.add_argument("--ip", type=str, default="127.0.0.1", help="Server IP address")
    parser.add_argument("--port", type=int, default=8000, help="Server port")
    parser.add_argument(
        "directory", nargs=1, type=str, help="Directory containing database files"
    )
    parser.add_argument("--x", required=False, help="Default X-axis parameter")
    parser.add_argument("--y", required=False, help="Default Y-axis parameter")
    parser.add_argument(
        "--refresh-interval",
        type=int,
        default=0,
        help="Auto-refresh interval in seconds",
    )
    parser.add_argument("--debug", action="store_true", help="Enable debug mode")

    return parser.parse_args()


def configure_app_from_args(args):
    """Configure the global config from command-line arguments."""
    _config.serverip = args.ip
    _config.serverport = args.port
    _config.directory = args.directory[0]
    _config.x = args.x
    _config.y = args.y
    _config.refresh_interval = args.refresh_interval


def create_dash_app():
    """Create and configure the Dash application."""
    app = Dash(
        __name__,
        external_stylesheets=[
            dbc_themes_url[_config.theme],
            dbc.icons.BOOTSTRAP,
        ],
    )
    app.server.logger.setLevel("INFO")
    return app


# ============================================================================
# MAIN EXECUTION
# ============================================================================


def main():
    """Main application entry point."""
    try:
        # Parse arguments and configure
        args = parse_arguments()
        configure_app_from_args(args)

        # Create and configure Dash app
        app = create_dash_app()
        register_callbacks(app)
        create_layout(app)

        # Run the application
        print("Starting Raelize Glitch Analyzer v2.1")
        print(f"Server: http://{_config.serverip}:{_config.serverport}")
        print(f"Database directory: {_config.directory}")

        app.run(host=_config.serverip, port=_config.serverport, debug=args.debug)

    except Exception as e:
        print(f"Error starting application: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
