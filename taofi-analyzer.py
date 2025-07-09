#!/usr/bin/env python3
import argparse
import os
import re
import sqlite3
import sys
from contextlib import closing
from dataclasses import asdict, dataclass, field
from operator import itemgetter
from os import listdir
from pathlib import Path
from typing import Dict

import dash_bootstrap_components as dbc
import numpy as np
import pandas as pd
import plotly.express as px
from dash import Dash, Input, Output, State, dcc, html
from dash import callback_context as ctx
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

_RECORDS = None
_config = AnalyzerConfig()

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


def update_legend_labels(fig, labels):
    for entry in fig.data:
        if entry["name"] in labels:
            entry["name"] = labels[entry["name"]]


def get_number_of_experiments(directory, database):
    database_path = os.path.join(directory, database)

    try:
        with closing(sqlite3.connect(database_path)) as connection:
            with closing(connection.cursor()) as cursor:
                cursor.execute("SELECT COUNT(*) FROM experiments")
                return cursor.fetchone()[0]
    except Exception as e:
        print("ERROR (get_number_of_experiments): %s" % (e))


# TODO: add date
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


def recolor(record, regex, new_color, fixgreen):
    if regex in [None, ""]:
        return record["color"]
    if fixgreen and record["color"] == "G":
        return record["color"]
    elif re.search(regex.encode(), record["response"]):
        return new_color
    else:
        return record["color"]


# def get_variable_names(record):
#     variable_names['id'] = get_variable_name(record, ['id'])
#     variable_names['color'] = get_variable_name(record, ['color'])
#     variable_names['delay'] = get_variable_name(record, ['delay', 'glitch_delay'])
#     variable_names['length'] = get_variable_name(record, ['length', 'glitch_length'])
#     variable_names['voltage'] = get_variable_name(record, ['voltage', 'glitch_voltage'])
#     variable_names['power'] = get_variable_name(record, ['power', 'glitch_power'])

#     return variable_names


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

    def get_variable_name(self, record, names):
        for name in names:
            if name in record:
                return name
        else:
            return None


def glitch_parameter_present(record, parameter):
    if parameter in record and record[parameter] not in [0, None]:
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


def generate_data(records, squeeze_records, response_s, response_e):
    v = VariableNames(records[0])

    has_normal = glitch_parameter_present(records[0], v.normal)
    has_length = glitch_parameter_present(records[0], v.length)
    has_power = glitch_parameter_present(records[0], v.power)
    has_voltage = glitch_parameter_present(records[0], v.voltage)
    has_reset = glitch_parameter_present(records[0], v.reset)

    if not squeeze_records:
        new_records = []

        for record in records:
            new_record = {}
            new_record["id"] = record[v.id]
            new_record["color"] = record[v.color]
            new_record["delay"] = record[v.delay]
            if has_normal:
                new_record["normal"] = record[v.normal]
            if has_length:
                new_record["length"] = record[v.length]
            if has_power:
                new_record["power"] = record[v.power]
            if has_voltage:
                new_record["voltage"] = record[v.voltage]
            if has_reset:
                new_record["reset"] = record[v.reset]
            new_record["rlen"] = len(v.response)

            # slice response
            response = slice_response(record[v.response], response_s, response_e)

            new_record["response"] = response.decode("utf-8", errors="replace")
            new_record["hex(response)"] = response.hex(" ")

            new_records.append(new_record)

        return new_records
    else:
        squeezed_records = {}

        for record in records:
            response = record[v.response].decode("utf-8", errors="replace")

            # slice response
            response = slice_response(response, response_s, response_e)

            if response not in squeezed_records:
                squeezed_records[response] = {}
                squeezed_records[response]["amount"] = 1
                squeezed_records[response]["color"] = record[v.color]
                squeezed_records[response]["Min(Delay)"] = record[v.delay]
                squeezed_records[response]["Max(Delay)"] = record[v.delay]
                if has_normal:
                    squeezed_records[response]["Min(Normal)"] = record[v.normal]
                    squeezed_records[response]["Max(Normal)"] = record[v.normal]
                if has_length:
                    squeezed_records[response]["Min(Length)"] = record[v.length]
                    squeezed_records[response]["Max(Length)"] = record[v.length]
                if has_power:
                    squeezed_records[response]["Min(Power)"] = record[v.power]
                    squeezed_records[response]["Max(Power)"] = record[v.power]
                if has_voltage:
                    squeezed_records[response]["Min(Voltage)"] = record[v.voltage]
                    squeezed_records[response]["Max(Voltage)"] = record[v.voltage]
                if has_reset:
                    squeezed_records[response]["Min(Reset)"] = record[v.reset]
                    squeezed_records[response]["Max(Reset)"] = record[v.reset]

                squeezed_records[response]["response"] = response
                squeezed_records[response]["hex(response)"] = record[v.response].hex(
                    " "
                )
            else:
                squeezed_records[response]["amount"] += 1
                squeezed_records[response]["Min(Delay)"] = min(
                    squeezed_records[response]["Min(Delay)"], record[v.delay]
                )
                squeezed_records[response]["Max(Delay)"] = max(
                    squeezed_records[response]["Max(Delay)"], record[v.delay]
                )
                if has_normal:
                    squeezed_records[response]["Min(Normal)"] = min(
                        squeezed_records[response]["Min(Normal)"], record[v.normal]
                    )
                    squeezed_records[response]["Max(Normal)"] = max(
                        squeezed_records[response]["Max(Normal)"], record[v.normal]
                    )
                if has_length:
                    squeezed_records[response]["Min(Length)"] = min(
                        squeezed_records[response]["Min(Length)"], record[v.length]
                    )
                    squeezed_records[response]["Max(Length)"] = max(
                        squeezed_records[response]["Max(Length)"], record[v.length]
                    )
                if has_power:
                    squeezed_records[response]["Min(Power)"] = min(
                        squeezed_records[response]["Min(Power)"], record[v.power]
                    )
                    squeezed_records[response]["Max(Power)"] = max(
                        squeezed_records[response]["Max(Power)"], record[v.power]
                    )
                if has_voltage:
                    squeezed_records[response]["Min(Voltage)"] = min(
                        squeezed_records[response]["Min(Voltage)"], record[v.voltage]
                    )
                    squeezed_records[response]["Max(Voltage)"] = max(
                        squeezed_records[response]["Max(Voltage)"], record[v.voltage]
                    )
                if has_reset:
                    squeezed_records[response]["Min(Reset)"] = min(
                        squeezed_records[response]["Min(Reset)"], record[v.reset]
                    )
                    squeezed_records[response]["Max(Reset)"] = max(
                        squeezed_records[response]["Max(Reset)"], record[v.reset]
                    )

        return sorted(squeezed_records.values(), key=itemgetter("amount"), reverse=True)


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


def update_global_records(config):
    global _RECORDS

    if not os.path.isfile(f"{config.directory}/{config.database}"):
        raise PreventUpdate

    con = sqlite3.connect(f"{config.directory}/{config.database}")

    # add some functions to sqlite
    con.create_function("match_string", 2, match_string)
    con.create_function("match_hex", 2, match_hex)

    # perform the query based on the query extension
    if config.query == "":
        query = "SELECT * FROM experiments;"
    else:
        query = f"SELECT * FROM experiments WHERE {config.query};"

    # read stuff from database
    try:
        df = pd.read_sql(query, con)
        con.close()
    except:
        raise PreventUpdate

    # add some noise
    exclude_from_jitter = ["color"]
    if config.x not in exclude_from_jitter and config.y not in exclude_from_jitter:
        df[config.x] += np.random.normal(0, config.jitter, df.shape[0])
        df[config.y] += np.random.normal(0, config.jitter, df.shape[0])

    # store records from global
    _RECORDS = df.to_dict("records")


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
        [Input("graph", "relayoutData"), Input("graph", "figure")],
        prevent_initial_call=True,
    )
    def zoomed_points(relayoutData, figure):
        if not figure or "xaxis.range[0]" not in relayoutData:
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
        ],
        [State(f"recolor-{color}", "value") for color in _COLORS]
        + [State(f"recolor-{color}-label", "value") for color in _COLORS],
        prevent_initial_call=True,
    )
    def update_graph(store, x, y, fixgreen, *color_states):
        global _RECORDS

        config = AnalyzerConfig(**store)

        if ctx.triggered_id == "config-store":
            # update x and y
            x = config.x
            y = config.y

        color_values = color_states[:8]
        color_labels = color_states[8:]

        # prevent update
        if any(v is None for v in [x, y]):
            raise PreventUpdate

        update_global_records(config)

        # color amounts
        colors = {
            "P": 0,
            "G": 0,
            "Y": 0,
            "M": 0,
            "O": 0,
            "C": 0,
            "B": 0,
            "Z": 0,
            "R": 0,
        }

        color_map = dict(zip(_COLORS, ["G", "Y", "M", "O", "C", "B", "Z", "R"]))

        # recolor if needed
        for record in _RECORDS:
            for value, color_code in zip(color_values, color_map.values()):
                record["color"] = recolor(record, value, color_code, fixgreen)
            colors[record["color"]] += 1

        # output plot
        try:
            fig = px.scatter(
                _RECORDS,
                x=x,
                y=y,
                render_mode="webgl",
                color="color",
                labels={
                    "color": f"Classification ({len(_RECORDS):,})",
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
                    "color": ["P", "G", "Y", "M", "O", "C", "B", "Z", "R"]
                },
            )
        except:
            raise PreventUpdate

        # update title of graph
        # fig.update_layout(title_text=config.database[:-7], title_x=0.5, title_y=0.95)
        fig.update_layout(title_text="")

        if config.x == "x" or config.y == "y":
            fig.update_xaxes(title_standoff=0, side="top")
            fig.update_yaxes(title_standoff=0, autorange="reversed")

        # Update legend labels
        labels = {}
        for color_code, value, label in zip(
            color_map.values(), color_values, color_labels
        ):
            count = colors[color_code]
            if label in ["", None]:
                label = value
            labels[color_code] = f"{label} ( {count} / {count / len(_RECORDS):.1%} )"
        labels["P"] = f"timeout ( {colors['P']} / {colors['P'] / len(_RECORDS):.1%} )"
        update_legend_labels(fig, labels)

        return fig

    # callback data; chained from update_graph()
    @app.callback(
        Output("data", "children"),
        [
            Input("config-store", "data"),
            Input("graph", "figure"),
            Input("switch-squeezedata", "value"),
            Input("switch-showhexdata", "value"),
            Input("switch-wraptext", "value"),
            Input("response_s", "value"),
            Input("response_e", "value"),
        ],
        prevent_initial_call=True,
    )
    def update_data(store, figure, squeeze, showhex, wraptext, response_s, response_e):
        global _RECORDS

        if any(x is None for x in [figure, _RECORDS]):
            raise PreventUpdate

        # squeeze data (or not)
        data = generate_data(_RECORDS, squeeze, response_s, response_e)

        # get columns from _RECORDS
        columns = data[0].keys()

        fields = []
        configs = []

        for column in columns:
            fields.append(column)
            if column in ["id", "color", "normal", "delay", "length", "power", "rlen"]:
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
                            "autoSize": True,
                        }
                    )
            elif column in ["hex(response)"]:
                configs.append({"autoSize": False, "width": 500, "hide": not showhex})
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

        data = AgGrid(
            columnDefs=columnDefs,
            rowData=data,
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
                "animateRows": False,
                "alwaysShowHorizontalScroll": True,
                "autoSizeStrategy": resize_strategy,
                "enableCellTextSelection": True,
                "ensureDomOrder": True,
            },
            style={"height": "1000px"},
        )

        return data


#
# Layout
#


def create_layout(app):
    app.layout = html.Div(
        [
            dcc.Store(id="config-store", data=asdict(_config)),
            html.Div(
                [
                    html.H4("Research by Raelize"),
                ],
                style={"width": "80%", "border-style": "none", "margin": "0 auto"},
            ),
            html.Div(
                [
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
                                ),
                                html.Div(
                                    [
                                        dcc.Dropdown(
                                            id="x-dropdown",
                                            style={"width": "100%"},
                                            placeholder="x-axis",
                                        ),
                                        dcc.Dropdown(
                                            id="y-dropdown",
                                            style={"width": "100%"},
                                            placeholder="y-axis",
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
                            ]
                        )
                    ),
                    dbc.Card(
                        dbc.CardBody(
                            [
                                html.Center(
                                    [
                                        dcc.Graph(id="graph", style={"width": "80%"}),
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
                                        label="Fix green",
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
                    dbc.Card(
                        dbc.CardBody(
                            [
                                dbc.Switch(
                                    id="switch-squeezedata",
                                    value=True,
                                    label="Squeeze Data",
                                    style={
                                        "display": "inline-block",
                                        "marginRight": "20px",
                                    },
                                ),
                                dbc.Switch(
                                    id="switch-showhexdata",
                                    value=False,
                                    label="Show Hex",
                                    style={
                                        "display": "inline-block",
                                        "marginRight": "20px",
                                    },
                                ),
                                dbc.Switch(
                                    id="switch-wraptext",
                                    value=False,
                                    label="Wrap Text",
                                    style={
                                        "display": "inline-block",
                                        "marginRight": "20px",
                                    },
                                ),
                                dcc.Input(
                                    id="response_s",
                                    type="number",
                                    placeholder="start",
                                    style={"width": "50px", "marginRight": "20px"},
                                    persistence=True,
                                ),
                                dcc.Input(
                                    id="response_e",
                                    type="number",
                                    placeholder="end",
                                    style={"width": "50px", "marginRight": "20px"},
                                    persistence=True,
                                ),
                                html.Div(
                                    id="data",
                                    style={
                                        "width": "100%",
                                        "height": "100%",
                                        "border-style": "none",
                                    },
                                ),
                            ]
                        )
                    ),
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
                    ),
                ],
                style={"width": "80%", "border-style": "none", "margin": "0 auto"},
            ),
        ],
        style={
            "width": "100%",
            "border-style": "none",
            "margin-top": "100px",
            "margin-bottom": "100px",
        },
    )


def check_env() -> None:
    required = ["ANALYZER_DIRECTORY"]
    missing = [var for var in required if var not in os.environ]
    if missing:
        raise ValueError(f"Missing required environment variables: {missing}")


#
# App
#

app = Dash(__name__, external_stylesheets=[dbc.themes.JOURNAL])
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
